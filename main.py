"""
v2 — Polymarket Copy Trading Bot
=================================
Strateji: Haftalık/aylık en kazançlı trader'ların açık pozisyonlarını çek,
aynı markette hemfikir olan trader sayısına göre sırala (konsensüs),
kapalı pozisyonlarla trader güvenilirliğini ölç, bet büyüklüğü öner.

Çalıştırma:
  python3 v2/main.py                     # tam çalıştırma
  python3 v2/main.py --dry-run           # sadece sinyal listele, emir yok
  python3 v2/main.py --min-consensus 3   # en az 3 trader hemfikir olsun
  python3 v2/main.py --period week       # sadece haftalık kazananlar
  python3 v2/main.py --bankroll 1000     # bankroll $1000 ile bet büyüklüğü hesapla

API'ler (tarayıcıdan yakalanan, auth gerekmez):
  GET https://data-api.polymarket.com/v1/biggest-winners?timePeriod={week|month|all}&limit=20
  GET https://data-api.polymarket.com/positions?user={wallet}&sortBy=CURRENT&sortDirection=DESC
  GET https://data-api.polymarket.com/closed-positions?user={wallet}&sortBy=realizedpnl&sortDirection=DESC

Güven skoru nasıl çalışır?
  - Her trader'ın kapalı pozisyonları çekilir (en fazla 50 trade)
  - Win rate, ortalama ROI, tutarlılık hesaplanır
  - Konsensüs sinyali: trader güven skorlarının ağırlıklı ortalaması
  - Bet büyüklüğü: half-Kelly × bankroll × güven faktörü
"""

import argparse
import datetime
import json
import os
import sys
import time
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

# ─── Varsayılan ayarlar (--arg ile override edilebilir) ──────────────────────
DEFAULT_TOP_N          = 20       # Kaç trader taransın (her dönem için)
DEFAULT_MIN_VALUE      = 1_000    # Aktif pozisyon için min $ (currentValue)
DEFAULT_MIN_CONSENSUS  = 2        # Konsensüs için min trader sayısı
DEFAULT_PERIODS        = ["week", "month"]  # Hangi dönemler
DEFAULT_OUTPUT         = "v2/v2_signals.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:149.0) "
        "Gecko/20100101 Firefox/149.0"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
    "Origin": "https://polymarket.com",
    "Referer": "https://polymarket.com/",
}

REQUEST_DELAY = 0.25   # saniye — rate limit için

# Bet büyüklüğü hesabı için
DEFAULT_BANKROLL    = 1_000.0   # $
DEFAULT_MAX_BET_PCT = 0.10      # bankroll'un max %10'u tek pozisyona
DEFAULT_KELLY_FRAC  = 0.5       # half-Kelly güvenlik marjı
CLOSED_POSITIONS_LIMIT = 50     # Trader başına kaç kapalı pozisyon analiz edilsin


# ─── HTTP yardımcısı ─────────────────────────────────────────────────────────

def fetch_json(url: str, timeout: int = 10) -> Any:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


# ─── API çağrıları ────────────────────────────────────────────────────────────

def fetch_top_traders(periods: list[str], top_n: int) -> dict[str, dict]:
    """
    Birden fazla dönem için biggest-winners çeker, wallet bazında deduplicate eder.
    En yüksek PnL dönemini kullanır.
    """
    wallets: dict[str, dict] = {}
    for period in periods:
        url = (
            f"https://data-api.polymarket.com/v1/biggest-winners"
            f"?timePeriod={period}&limit={top_n}&offset=0&category=overall"
        )
        try:
            data = fetch_json(url)
            for d in data:
                w   = d["proxyWallet"]
                pnl = float(d.get("pnl", 0))
                if w not in wallets or pnl > wallets[w]["best_pnl"]:
                    wallets[w] = {
                        "wallet":    w,
                        "name":      d.get("userName", w[:12] + "..."),
                        "best_pnl":  pnl,
                        "period":    period,
                    }
        except Exception as e:
            print(f"  [warn] fetch_top_traders({period}): {e}")
        time.sleep(REQUEST_DELAY)
    return wallets


def fetch_open_positions(wallet: str, min_value: float = 100) -> list[dict]:
    """Trader'ın aktif açık pozisyonlarını çeker (currentValue >= min_value)."""
    url = (
        f"https://data-api.polymarket.com/positions"
        f"?user={wallet}&sortBy=CURRENT&sortDirection=DESC"
        f"&sizeThreshold=.1&limit=100&offset=0"
    )
    try:
        data = fetch_json(url)
        return [p for p in data if float(p.get("currentValue", 0)) >= min_value]
    except Exception as e:
        print(f"  [warn] fetch_open_positions({wallet[:16]}...): {e}")
        return []


def fetch_closed_positions(wallet: str, limit: int = CLOSED_POSITIONS_LIMIT) -> list[dict]:
    """Trader'ın realized PnL'e göre sıralanmış kapalı pozisyonlarını çeker."""
    url = (
        f"https://data-api.polymarket.com/closed-positions"
        f"?user={wallet}&sortBy=realizedpnl&sortDirection=DESC"
        f"&limit={limit}&offset=0"
    )
    try:
        return fetch_json(url)
    except Exception as e:
        print(f"  [warn] fetch_closed_positions({wallet[:16]}...): {e}")
        return []


# ─── Trader geçmiş analizi ───────────────────────────────────────────────────

def analyze_trader_history(wallet: str, name: str) -> dict:
    """
    Trader'ın kapalı pozisyonlarına bakarak güvenilirlik metriklerini hesaplar.

    Döndürülen sözlük:
      wins          : Kazanılan trade sayısı
      losses        : Kaybedilen trade sayısı
      total         : Toplam analiz edilen trade
      win_rate      : wins / total  (0.0 – 1.0)
      avg_roi       : Ortalama ROI (kazanç / yatırım)
      total_pnl     : Toplam realized PnL ($)
      total_invested: Toplam yatırılan ($)
      consistency   : 1 - (loss_pnl / total_pnl) normalise — yüksek = tutarlı
      trust_score   : Genel güven skoru (0 – 100)
    """
    closed = fetch_closed_positions(wallet)
    time.sleep(REQUEST_DELAY)

    if not closed:
        return _empty_history(name)

    wins = losses = 0
    total_pnl = 0.0
    total_invested = 0.0
    rois: list[float] = []

    for pos in closed:
        pnl      = float(pos.get("realizedPnl", 0) or 0)
        invested = float(pos.get("totalBought", 0) or 0)
        cur_p    = float(pos.get("curPrice", 0) or 0)   # 1.0 = kazandı, 0.0 = kaybetti

        total_pnl      += pnl
        total_invested += invested

        if cur_p >= 0.95:        # pozisyon çözümlendi: kazandı
            wins += 1
            if invested > 0:
                rois.append(pnl / invested)
        elif cur_p <= 0.05:      # pozisyon çözümlendi: kaybetti
            losses += 1
            if invested > 0:
                rois.append(pnl / invested)
        # Aradaki değerler henüz çözümlenmemiş → sayma

    total = wins + losses
    if total == 0:
        return _empty_history(name)

    win_rate  = wins / total
    avg_roi   = sum(rois) / len(rois) if rois else 0.0

    # Tutarlılık: kazanan trade'lerin PnL payı
    win_pnl  = sum(float(p.get("realizedPnl", 0) or 0) for p in closed
                   if float(p.get("curPrice", 0) or 0) >= 0.95)
    loss_pnl = abs(sum(float(p.get("realizedPnl", 0) or 0) for p in closed
                       if float(p.get("curPrice", 0) or 0) <= 0.05))
    consistency = win_pnl / (win_pnl + loss_pnl) if (win_pnl + loss_pnl) > 0 else 0.5

    # Güven skoru (0-100):
    #   40 puan → win_rate  (0.5 = 0 puan, 1.0 = 40 puan)
    #   30 puan → avg_roi   (0 = 0, ≥0.5 = 30 puan)
    #   20 puan → tutarlılık
    #   10 puan → trade sayısı (≥20 = tam)
    wr_pts    = max(0, (win_rate - 0.5) / 0.5) * 40
    roi_pts   = min(1, max(0, avg_roi) / 0.5) * 30
    cons_pts  = consistency * 20
    vol_pts   = min(1, total / 20) * 10
    trust     = wr_pts + roi_pts + cons_pts + vol_pts

    return {
        "name":           name,
        "wins":           wins,
        "losses":         losses,
        "total":          total,
        "win_rate":       round(win_rate, 4),
        "avg_roi":        round(avg_roi, 4),
        "total_pnl":      round(total_pnl, 2),
        "total_invested": round(total_invested, 2),
        "consistency":    round(consistency, 4),
        "trust_score":    round(trust, 1),
    }


def _empty_history(name: str) -> dict:
    return {
        "name": name, "wins": 0, "losses": 0, "total": 0,
        "win_rate": 0.0, "avg_roi": 0.0, "total_pnl": 0.0,
        "total_invested": 0.0, "consistency": 0.5, "trust_score": 0.0,
    }


# ─── Bet büyüklüğü hesabı ────────────────────────────────────────────────────

def suggest_bet(
    signal: dict,
    histories: dict[str, dict],
    bankroll: float,
    max_pct: float = DEFAULT_MAX_BET_PCT,
    kelly_frac: float = DEFAULT_KELLY_FRAC,
) -> dict:
    """
    Konsensüs sinyali için tavsiye edilen bet büyüklüğünü hesaplar.

    Yöntem (half-Kelly tabanlı):
      1. Sinyaldeki trader'ların ağırlıklı güven skoru → ortalama_guven (0-100)
      2. Güven → olasılık tahmini  p = 0.50 + (guven/100) * 0.30
         (En iyi trader → max %80 güven. Polymarket'ta edge %30'dan büyük olmaz.)
      3. Ortalama giriş fiyatı → ödeme katsayısı  b = (1/price) - 1
      4. Kelly oranı: f = (b*p - (1-p)) / b
      5. Bet = bankroll × f × kelly_frac   (max_pct ile tavan)

    Döndürür: { "bet_usd", "confidence_pct", "edge_pct", "kelly_f", "note" }
    """
    positions = signal["positions"]
    prices    = [float(p.get("avgPrice", 0.5)) for p in positions]
    avg_price = sum(prices) / len(prices) if prices else 0.5

    # Trader güven skorları
    scores = []
    for p in positions:
        w    = p.get("_wallet", "")
        hist = histories.get(w)
        if hist and hist["total"] >= 3:
            scores.append(hist["trust_score"])

    if not scores:
        avg_trust = 20.0  # Geçmişi olmayan trader → düşük güven
    else:
        avg_trust = sum(scores) / len(scores)

    # Konsensüs güçlendirmesi: her ekstra trader +5 puan
    n_bonus   = (signal["n_traders"] - 2) * 5
    adj_trust = min(95, avg_trust + n_bonus)

    # p tahmini: 0.50 base + trust katkısı (max +0.30)
    p_win = 0.50 + (adj_trust / 100) * 0.30

    # Ödeme katsayısı (NO/YES token'ı 1.0'a giderse)
    if avg_price <= 0 or avg_price >= 1:
        avg_price = 0.5
    b = (1.0 / avg_price) - 1.0        # örn. price=0.40 → b=1.5

    # Kelly
    kelly_f = (b * p_win - (1 - p_win)) / b
    kelly_f = max(0, kelly_f)           # negatif → bahis yok

    bet_raw  = bankroll * kelly_f * kelly_frac
    bet_capped = min(bet_raw, bankroll * max_pct)

    edge_pct = (p_win - avg_price) / avg_price * 100  # tahmini EV%

    note = "✅ GİR" if (kelly_f > 0 and edge_pct > 5) else "⚠️ ZAYIF EDGE"
    if signal["n_traders"] >= 4:
        note += " (güçlü konsensüs)"

    return {
        "bet_usd":        round(bet_capped, 2),
        "confidence_pct": round(adj_trust, 1),
        "p_win":          round(p_win, 3),
        "edge_pct":       round(edge_pct, 1),
        "kelly_f":        round(kelly_f, 4),
        "note":           note,
    }


# ─── Konsensüs motoru ────────────────────────────────────────────────────────

def score_position_group(positions: list[dict], trader_map: dict[str, dict]) -> float:
    """
    Bir marketteki pozisyon grubuna skor atar.
    Faktörler:
      - Kaç farklı trader hemfikir     (ağırlık x4)
      - Toplam aktif değer ($)         (x0.0001 → normalise)
      - En iyi trader'ın PnL'i        (x0.00001)
      - Pozisyonların ort PnL%        (pozitifse bonus)
    """
    n      = len(positions)
    total  = sum(float(p.get("currentValue", 0)) for p in positions)
    pnls   = [float(p.get("percentPnl", 0)) for p in positions]
    avg_pnl = sum(pnls) / n if n else 0
    best_trader_pnl = max(
        trader_map.get(p.get("_wallet", ""), {}).get("best_pnl", 0)
        for p in positions
    )
    return (
        n * 4.0
        + total * 0.0001
        + best_trader_pnl * 0.00001
        + max(0, avg_pnl) * 0.1
    )


def find_consensus(
    all_positions: list[dict],
    trader_map: dict[str, dict],
    min_traders: int = 2,
    same_direction: bool = True,
) -> list[dict]:
    """
    Birden fazla top trader'ın aynı markette (ve aynı yönde) pozisyon tuttuğu
    fırsatları bulur, skora göre sıralar.
    """
    groups: dict[tuple, list] = defaultdict(list)
    for p in all_positions:
        cid     = p.get("conditionId", "")
        outcome = p.get("outcome", "?")
        key     = (cid, outcome) if same_direction else (cid,)
        groups[key].append(p)

    results = []
    for key, positions in groups.items():
        # Aynı wallet'tan gelen duplikatları kaldır
        seen: set[str] = set()
        unique = []
        for p in positions:
            w = p.get("_wallet", "")
            if w not in seen:
                seen.add(w)
                unique.append(p)

        if len(unique) < min_traders:
            continue

        score = score_position_group(unique, trader_map)
        results.append({
            "conditionId": key[0],
            "outcome":     key[1] if same_direction else unique[0].get("outcome", "?"),
            "positions":   unique,
            "n_traders":   len(unique),
            "score":       score,
        })

    return sorted(results, key=lambda x: -x["score"])


# ─── Raporlama ────────────────────────────────────────────────────────────────

def print_consensus(
    consensus: list[dict],
    histories: dict[str, dict] | None = None,
    bankroll: float = DEFAULT_BANKROLL,
) -> None:
    if not consensus:
        print("  (Konsensüs sinyal bulunamadı)")
        return

    for i, c in enumerate(consensus, 1):
        ps      = c["positions"]
        p0      = ps[0]
        title   = p0.get("title", "?")[:60]
        outcome = c["outcome"]
        prices  = [float(p.get("avgPrice", 0)) for p in ps]
        avg_p   = sum(prices) / len(prices)
        total   = sum(float(p.get("currentValue", 0)) for p in ps)
        pnl_avg = sum(float(p.get("percentPnl", 0)) for p in ps) / len(ps)

        print(f"  #{i:02d}  [{outcome:12} @ {avg_p:.2f}]  Score:{c['score']:.0f}")
        print(f"        {title}")
        print(f"        Toplam pozisyon: ${total:>10,.0f}  |  Ort PnL: {pnl_avg:>+6.1f}%")

        # Trader bazlı güven detayı
        for p in ps:
            w      = p.get("_wallet", "")
            trader = p.get("_trader", "?")
            cv     = float(p.get("currentValue", 0))
            ppnl   = float(p.get("percentPnl", 0))
            hist   = (histories or {}).get(w, {})
            wr     = hist.get("win_rate", 0)
            ts     = hist.get("trust_score", 0)
            n_tr   = hist.get("total", 0)
            roi    = hist.get("avg_roi", 0)
            if n_tr > 0:
                hist_str = (
                    f"güven={ts:.0f}/100  "
                    f"WR={wr:.0%}  "
                    f"avgROI={roi:+.0%}  "
                    f"({n_tr} trade)"
                )
            else:
                hist_str = "geçmiş veri yok"
            print(f"          • {trader:22}  ${cv:>9,.0f}  {ppnl:>+6.1f}%   [{hist_str}]")

        # Bet tavsiyesi
        if histories is not None:
            bet = suggest_bet(c, histories, bankroll)
            verdict = bet["note"]
            print(
                f"        → {verdict}  |  "
                f"Güven: {bet['confidence_pct']:.0f}/100  |  "
                f"p_win: {bet['p_win']:.1%}  |  "
                f"Edge: {bet['edge_pct']:+.1f}%  |  "
                f"Tavsiye: ${bet['bet_usd']:.0f} "
                f"(Kelly×0.5={bet['kelly_f']:.3f})"
            )
        print()


def print_all_signals(all_positions: list[dict], top_n: int = 20) -> None:
    ranked = sorted(all_positions, key=lambda p: -float(p.get("currentValue", 0)))
    for p in ranked[:top_n]:
        title   = p.get("title", "?")[:55]
        outcome = p.get("outcome", "?")
        avg_p   = float(p.get("avgPrice", 0))
        cv      = float(p.get("currentValue", 0))
        pnl     = float(p.get("percentPnl", 0))
        trader  = p.get("_trader", "?")
        print(f"  [{outcome:12} @ {avg_p:.2f}] {title}")
        print(f"    ${cv:>10,.0f}  PnL%={pnl:>+6.1f}%  ← {trader}")


# ─── Kaydetme ─────────────────────────────────────────────────────────────────

def save_signals(
    consensus: list[dict],
    all_positions: list[dict],
    traders_scanned: int,
    output_path: str,
) -> None:
    def pos_to_dict(p: dict) -> dict:
        return {
            "conditionId":  p.get("conditionId", ""),
            "title":        p.get("title", "?"),
            "outcome":      p.get("outcome", "?"),
            "avg_price":    round(float(p.get("avgPrice", 0)), 4),
            "current_value": round(float(p.get("currentValue", 0)), 2),
            "initial_value": round(float(p.get("initialValue", 0)), 2),
            "percent_pnl":  round(float(p.get("percentPnl", 0)), 2),
            "trader":       p.get("_trader", "?"),
            "wallet":       p.get("_wallet", "?"),
        }

    output = {
        "generated_at":       datetime.datetime.utcnow().isoformat() + "Z",
        "traders_scanned":    traders_scanned,
        "active_positions":   len(all_positions),
        "consensus_count":    len(consensus),
        "consensus_signals":  [
            {
                "conditionId":   c["conditionId"],
                "title":         c["positions"][0].get("title", "?"),
                "outcome":       c["outcome"],
                "avg_price":     round(
                    sum(float(p.get("avgPrice", 0)) for p in c["positions"])
                    / len(c["positions"]), 4
                ),
                "n_traders":     c["n_traders"],
                "total_value":   round(
                    sum(float(p.get("currentValue", 0)) for p in c["positions"]), 2
                ),
                "avg_pnl_pct":   round(
                    sum(float(p.get("percentPnl", 0)) for p in c["positions"])
                    / len(c["positions"]), 2
                ),
                "score":         round(c["score"], 2),
                "traders":       [p.get("_trader", "?") for p in c["positions"]],
                "bet_suggestion": c.get("bet_suggestion", {}),
            }
            for c in consensus
        ],
        "all_signals": [pos_to_dict(p) for p in sorted(
            all_positions, key=lambda x: -float(x.get("currentValue", 0))
        )],
    }

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)


# ─── Ana akış ────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="v2 Copy Trading Bot")
    parser.add_argument("--top-n",         type=int,   default=DEFAULT_TOP_N,
                        help="Kaç trader taransın (her dönem için)")
    parser.add_argument("--min-value",     type=float, default=DEFAULT_MIN_VALUE,
                        help="Min aktif pozisyon değeri ($)")
    parser.add_argument("--min-consensus", type=int,   default=DEFAULT_MIN_CONSENSUS,
                        help="Konsensüs için min trader sayısı")
    parser.add_argument("--period",        nargs="+",  default=DEFAULT_PERIODS,
                        choices=["week", "month", "all"],
                        help="Hangi dönemler (örn. --period week month)")
    parser.add_argument("--same-direction", action="store_true", default=True,
                        help="Konsensüste aynı yön zorunlu mu (varsayılan: evet)")
    parser.add_argument("--output",        default=DEFAULT_OUTPUT,
                        help="Çıktı JSON dosyası")
    parser.add_argument("--bankroll",      type=float, default=DEFAULT_BANKROLL,
                        help="Toplam bankroll ($), bet büyüklüğü hesabı için")
    parser.add_argument("--max-bet-pct",  type=float, default=DEFAULT_MAX_BET_PCT,
                        help="Tek pozisyon için bankroll'un max yüzdesi (0.10 = %%10)")
    parser.add_argument("--dry-run",       action="store_true",
                        help="Sadece raporla, CLOB emri gönderme")
    parser.add_argument("--no-save",       action="store_true",
                        help="JSON dosyasına kaydetme")
    parser.add_argument("--skip-history", action="store_true",
                        help="Kapalı pozisyon analizini atla (hızlı mod)")
    args = parser.parse_args()

    sep = "─" * 65
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print(f"\n{'═'*65}")
    print(f"  v2 — Polymarket Copy Trading Bot  ·  {now}")
    print(f"{'═'*65}\n")

    # ── 1. Top trader'ları çek ─────────────────────────────────────────────
    print(f"[1/3] Top trader'lar çekiliyor  (dönem: {args.period}, n={args.top_n})...")
    trader_map = fetch_top_traders(periods=args.period, top_n=args.top_n)
    traders_sorted = sorted(trader_map.values(), key=lambda t: -t["best_pnl"])
    print(f"  {len(trader_map)} unique trader bulundu\n")
    print(f"  {'İsim':22} {'PnL':>12}  {'Dönem'}")
    print(f"  {sep}")
    for t in traders_sorted[:10]:
        print(f"  {t['name']:22} ${t['best_pnl']:>11,.0f}  {t['period']}")
    if len(traders_sorted) > 10:
        print(f"  ... ve {len(traders_sorted)-10} trader daha")

    # ── 2. Açık pozisyonları tara ──────────────────────────────────────────
    print(f"\n[2/3] Açık pozisyonlar taranıyor  (min_value=${args.min_value:,.0f})...")
    all_positions: list[dict] = []
    active_count = 0

    for t in traders_sorted:
        positions = fetch_open_positions(t["wallet"], min_value=args.min_value)
        if positions:
            active_count += 1
            for p in positions:
                p["_wallet"] = t["wallet"]
                p["_trader"] = t["name"]
            all_positions.extend(positions)
            print(f"  {t['name']:22} ${t['best_pnl']:>10,.0f}  →  {len(positions):3d} pozisyon")
        time.sleep(REQUEST_DELAY)

    print(f"\n  Özet: {len(all_positions)} aktif pozisyon / {active_count} aktif trader")

    # ── 3. Konsensüs bul ───────────────────────────────────────────────────
    print(f"\n[3/3] Konsensüs analizi  (min={args.min_consensus} trader, aynı_yön={args.same_direction})...")
    consensus = find_consensus(
        all_positions,
        trader_map,
        min_traders=args.min_consensus,
        same_direction=args.same_direction,
    )

    # ── 4. Trader geçmiş analizi (kapalı pozisyonlar) ─────────────────────
    histories: dict[str, dict] = {}
    if not args.skip_history:
        # Sadece konsensüs sinyallerinde geçen trader'ları analiz et
        relevant_wallets: set[str] = set()
        for c in consensus:
            for p in c["positions"]:
                relevant_wallets.add(p.get("_wallet", ""))

        if relevant_wallets:
            print(f"\n[4/4] Trader güven analizi  ({len(relevant_wallets)} trader, kapalı pozisyonlar)...")
            for w in relevant_wallets:
                info = trader_map.get(w, {})
                name = info.get("name", w[:16] + "...")
                hist = analyze_trader_history(w, name)
                histories[w] = hist
                if hist["total"] > 0:
                    print(
                        f"  {name:22}  "
                        f"WR={hist['win_rate']:.0%}  "
                        f"avgROI={hist['avg_roi']:+.0%}  "
                        f"({hist['wins']}W/{hist['losses']}L)  "
                        f"güven={hist['trust_score']:.0f}/100"
                    )
                else:
                    print(f"  {name:22}  kapalı pozisyon verisi yok")
        else:
            print("\n[4/4] Konsensüs sinyal yok, trader analizi atlandı.")
    else:
        print("\n[4/4] --skip-history aktif, trader analizi atlandı.")

    # ── Rapor ──────────────────────────────────────────────────────────────
    print(f"\n{'═'*65}")
    print(f"  KONSENSÜS SİNYALLER — {len(consensus)} market  (min {args.min_consensus} trader)")
    if not args.skip_history:
        print(f"  Bankroll: ${args.bankroll:,.0f}  |  Max tek bet: %{args.max_bet_pct*100:.0f}")
    print(f"{'═'*65}\n")
    print_consensus(
        consensus,
        histories=histories if not args.skip_history else None,
        bankroll=args.bankroll,
    )

    if len(consensus) == 0 or args.min_consensus == 1:
        print(f"\n{'─'*65}")
        print(f"  EN BÜYÜK TEK-TRADER SİNYALLER (top 15)")
        print(f"{'─'*65}\n")
        print_all_signals(all_positions, top_n=15)

    # ── Kaydet ─────────────────────────────────────────────────────────────
    if not args.no_save:
        # Geçmiş verisini de sinyallere ekle
        enriched_consensus = []
        for c in consensus:
            bet = suggest_bet(c, histories, args.bankroll, args.max_bet_pct) if histories else {}
            enriched_consensus.append({**c, "bet_suggestion": bet})
        save_signals(enriched_consensus, all_positions, len(trader_map), args.output)
        print(f"\n  Kaydedildi → {args.output}")

    if args.dry_run:
        print(f"\n  [dry-run] CLOB emri gönderilmedi.")

    print(f"\n{'═'*65}\n")


if __name__ == "__main__":
    main()
