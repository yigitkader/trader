"""
v2 — Polymarket Copy Trading Bot
=================================
Strateji: Haftalık/aylık en kazançlı trader'ların açık pozisyonlarını çek,
aynı markette hemfikir olan trader sayısına göre sırala (konsensüs),
kapalı pozisyonlarla trader güvenilirliğini kategori bazlı ölç, bet büyüklüğü öner.

Çalıştırma:
  python3 main.py                      # tam çalıştırma
  python3 main.py --dry-run            # sadece sinyal listele, emir yok
  python3 main.py --min-consensus 3    # en az 3 trader hemfikir olsun
  python3 main.py --period week        # sadece haftalık kazananlar
  python3 main.py --bankroll 1000      # $1000 bankroll ile bet büyüklüğü hesapla
  python3 main.py --min-hours 24       # deadline'a en az 24 saat kalan marketler
  python3 main.py --no-cache           # cache'i atla, taze veri çek

Düzeltmeler (v2.1):
  - fetch_with_retry(): 429/5xx için exponential backoff
  - JSON cache: her wallet için TTL tabanlı önbellekleme (cache/ klasörü)
  - Kategori bazlı win rate: spor / siyaset / kripto / diğer ayrı ayrı
  - Doğru win/loss tespiti: curPrice yerine realizedPnl işaretine bakılıyor
  - Konsensüs skoru: boyuta değil trader kalitesine dayalı (n × avg_trust)
  - Paralel HTTP: ThreadPoolExecutor ile açık/kapalı pozisyonlar eş zamanlı
  - Deadline filtresi: endDate < now + min_hours olan marketler dışlanıyor
  - Price drift: avgPrice ile currentPrice arasındaki sapma gösteriliyor
"""

import argparse
import datetime
import hashlib
import json
import math
import time
import urllib.error
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

# ─── Varsayılan ayarlar ───────────────────────────────────────────────────────
DEFAULT_TOP_N         = 20
DEFAULT_MIN_VALUE     = 1_000      # $ — aktif pozisyon eşiği
DEFAULT_MIN_CONSENSUS = 2
DEFAULT_PERIODS       = ["week", "month"]
DEFAULT_OUTPUT        = "v2_signals.json"
DEFAULT_BANKROLL      = 1_000.0
DEFAULT_MAX_BET_PCT   = 0.10       # bankroll'un max %10'u
DEFAULT_KELLY_FRAC    = 0.5        # half-Kelly
DEFAULT_MIN_HOURS     = 4          # deadline'a en az N saat kalan marketler
CLOSED_LIMIT          = 50         # trader başına kapalı pozisyon sayısı
CACHE_DIR             = Path("cache")
CACHE_TTL_SECS        = 3600       # 1 saat cache geçerliliği
MAX_WORKERS           = 8          # paralel HTTP thread sayısı

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

# Market kategori anahtar kelimeleri
CATEGORY_KEYWORDS = {
    "sports":    ["nba", "nfl", "nhl", "mlb", "fc", "soccer", "football",
                  "tennis", "golf", "ufc", "mma", "boxing", "match", "series",
                  "playoff", "championship", "league", "celtics", "nuggets",
                  "knicks", "lakers", "hawks", "76ers", "timberwolves",
                  "bruins", "oilers", "wild", "stars", "ducks", "penguins"],
    "crypto":    ["bitcoin", "btc", "ethereum", "eth", "solana", "sol",
                  "crypto", "xrp", "price", "reach", "dip", "rally"],
    "politics":  ["president", "election", "senator", "congress", "vote",
                  "republican", "democrat", "trump", "biden", "vance",
                  "harris", "prime minister", "parliament", "minister",
                  "iran", "ukraine", "russia", "nato", "ceasefire",
                  "sanction", "war", "invasion", "military"],
}


# ─── HTTP: retry + backoff ────────────────────────────────────────────────────

def fetch_json(url: str, timeout: int = 10, max_retries: int = 3) -> Any:
    """Exponential backoff ile HTTP GET → JSON. 429/5xx'de retry yapar."""
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < max_retries - 1:
                wait = 2 ** attempt          # 1s, 2s, 4s
                time.sleep(wait)
            else:
                raise
        except Exception:
            if attempt < max_retries - 1:
                time.sleep(1)
            else:
                raise
    return []


# ─── Cache ────────────────────────────────────────────────────────────────────

def _cache_key(wallet: str, kind: str) -> Path:
    h = hashlib.md5(f"{wallet}:{kind}".encode()).hexdigest()[:16]
    CACHE_DIR.mkdir(exist_ok=True)
    return CACHE_DIR / f"{h}.json"


def _load_cache(wallet: str, kind: str) -> list | None:
    p = _cache_key(wallet, kind)
    if not p.exists():
        return None
    age = time.time() - p.stat().st_mtime
    if age > CACHE_TTL_SECS:
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def _save_cache(wallet: str, kind: str, data: list) -> None:
    try:
        _cache_key(wallet, kind).write_text(json.dumps(data))
    except Exception:
        pass


# ─── Market kategorisi ────────────────────────────────────────────────────────

def classify_market(title: str) -> str:
    """Başlık metninden market kategorisini tahmin eder."""
    low = title.lower()
    for cat, kws in CATEGORY_KEYWORDS.items():
        if any(kw in low for kw in kws):
            return cat
    return "other"


# ─── API çağrıları ────────────────────────────────────────────────────────────

def fetch_top_traders(periods: list[str], top_n: int) -> dict[str, dict]:
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
                        "wallet":   w,
                        "name":     d.get("userName", w[:12] + "..."),
                        "best_pnl": pnl,
                        "period":   period,
                    }
        except Exception as e:
            print(f"  [warn] fetch_top_traders({period}): {e}")
        time.sleep(0.3)
    return wallets


def _is_position_open(pos: dict, min_value: float) -> bool:
    """
    Pozisyonun gerçekten açık ve işlem yapılabilir olup olmadığını kontrol eder.
    Reddeder:
      - currentValue < min_value              (değer çok küçük)
      - endDate geçmişte (hours_left < 0)     (deadline geçmiş)
      - curPrice ≥ 0.95 veya ≤ 0.05          (market zaten çözümlenmiş)
    """
    cv = float(pos.get("currentValue") or 0)
    if cv < min_value:
        return False

    # Deadline kontrolü
    hl = _hours_to_deadline(pos.get("endDate"))
    if hl is not None and hl < 0:
        return False   # market süresi dolmuş

    # curPrice: 0 veya 1'e yakınsa market kapanmış demektir
    cur_p = pos.get("curPrice")
    if cur_p is not None:
        cur_p = float(cur_p)
        if cur_p >= 0.98 or cur_p <= 0.02:
            return False

    return True


def _fetch_open_one(args_tuple: tuple) -> tuple[str, list]:
    """(wallet, min_value, use_cache) → (wallet, truly_open_positions)"""
    wallet, min_value, use_cache = args_tuple
    if use_cache:
        cached = _load_cache(wallet, "open")
        if cached is not None:
            return wallet, [p for p in cached if _is_position_open(p, min_value)]

    url = (
        f"https://data-api.polymarket.com/positions"
        f"?user={wallet}&sortBy=CURRENT&sortDirection=DESC"
        f"&sizeThreshold=.1&limit=100&offset=0"
    )
    try:
        data = fetch_json(url)
        if use_cache:
            _save_cache(wallet, "open", data)
        return wallet, [p for p in data if _is_position_open(p, min_value)]
    except Exception as e:
        print(f"  [warn] open_positions({wallet[:16]}...): {e}")
        return wallet, []


def fetch_all_open_positions(
    traders: list[dict],
    min_value: float,
    use_cache: bool = True,
) -> list[dict]:
    """Tüm trader'ların açık pozisyonlarını paralel olarak çeker."""
    tasks  = [(t["wallet"], min_value, use_cache) for t in traders]
    result = []
    wallet_to_trader = {t["wallet"]: t for t in traders}

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(_fetch_open_one, task): task[0] for task in tasks}
        for fut in as_completed(futures):
            wallet, positions = fut.result()
            trader = wallet_to_trader.get(wallet, {})
            if positions:
                for p in positions:
                    p["_wallet"] = wallet
                    p["_trader"] = trader.get("name", wallet[:12] + "...")
                result.extend(positions)
                print(
                    f"  {trader.get('name','?'):22} "
                    f"${trader.get('best_pnl',0):>10,.0f}  →  {len(positions):3d} pozisyon"
                )
    return result


def _fetch_closed_one(args_tuple: tuple) -> tuple[str, list]:
    """(wallet, use_cache) → (wallet, closed_positions)"""
    wallet, use_cache = args_tuple
    if use_cache:
        cached = _load_cache(wallet, "closed")
        if cached is not None:
            return wallet, cached

    url = (
        f"https://data-api.polymarket.com/closed-positions"
        f"?user={wallet}&sortBy=realizedpnl&sortDirection=DESC"
        f"&limit={CLOSED_LIMIT}&offset=0"
    )
    try:
        data = fetch_json(url)
        if use_cache:
            _save_cache(wallet, "closed", data)
        return wallet, data
    except Exception as e:
        print(f"  [warn] closed_positions({wallet[:16]}...): {e}")
        return wallet, []


# ─── Trader geçmiş analizi ───────────────────────────────────────────────────

def _is_win(pos: dict) -> bool | None:
    """
    Kapalı pozisyonun kazanıp kazanmadığını güvenilir şekilde tespit eder.
    Önce realizedPnl işaretine, sonra curPrice'a bakar.
    None → henüz çözümlenmemiş.
    """
    pnl = float(pos.get("realizedPnl", 0) or 0)
    invested = float(pos.get("totalBought", 0) or 0)

    # 1. realizedPnl kesin bir işaret verir: yatırılanın %80'inden fazla
    #    kazanç/kayıp varsa çözümlenmiş sayıyoruz.
    if invested > 0:
        roi = pnl / invested
        if roi >= 0.5:       # büyük kazanç → kazandı
            return True
        if roi <= -0.7:      # büyük kayıp → kaybetti
            return False

    # 2. curPrice fallback
    cur_p = float(pos.get("curPrice", 0.5) or 0.5)
    if cur_p >= 0.95:
        return True
    if cur_p <= 0.05:
        return False

    return None   # belirsiz


def analyze_trader_history(wallet: str, name: str, closed: list[dict]) -> dict:
    """
    Kapalı pozisyonları kullanarak trader güvenilirlik metriklerini hesaplar.
    Kategori bazlı win rate içerir: sports / crypto / politics / other.
    """
    if not closed:
        return _empty_history(name)

    wins = losses = 0
    total_pnl = 0.0
    total_invested = 0.0
    rois: list[float] = []
    cat_stats: dict[str, dict] = {
        cat: {"wins": 0, "losses": 0}
        for cat in ("sports", "crypto", "politics", "other")
    }

    for pos in closed:
        pnl      = float(pos.get("realizedPnl", 0) or 0)
        invested = float(pos.get("totalBought", 0) or 0)
        outcome  = _is_win(pos)
        title    = pos.get("title", "") or ""
        cat      = classify_market(title)

        total_pnl      += pnl
        total_invested += invested

        if outcome is True:
            wins += 1
            cat_stats[cat]["wins"] += 1
            if invested > 0:
                rois.append(pnl / invested)
        elif outcome is False:
            losses += 1
            cat_stats[cat]["losses"] += 1
            if invested > 0:
                rois.append(pnl / invested)
        # outcome is None → henüz çözümlenmemiş → say ma

    total = wins + losses
    if total == 0:
        return _empty_history(name)

    win_rate = wins / total
    avg_roi  = sum(rois) / len(rois) if rois else 0.0

    # Tutarlılık: kazanılan PnL / toplam |PnL|
    win_pnl  = sum(float(p.get("realizedPnl", 0) or 0) for p in closed
                   if _is_win(p) is True)
    loss_pnl = abs(sum(float(p.get("realizedPnl", 0) or 0) for p in closed
                       if _is_win(p) is False))
    consistency = win_pnl / (win_pnl + loss_pnl) if (win_pnl + loss_pnl) > 0 else 0.5

    # Kategori bazlı win rate
    cat_wr = {}
    for cat, s in cat_stats.items():
        n_cat = s["wins"] + s["losses"]
        cat_wr[cat] = round(s["wins"] / n_cat, 4) if n_cat > 0 else None

    # Güven skoru (0-100):
    #   40 puan → win_rate  (0.5 = 0, 1.0 = 40)
    #   30 puan → avg_roi   (0 = 0, ≥0.5 = 30)
    #   20 puan → tutarlılık
    #   10 puan → trade hacmi (≥20 trade = tam puan)
    wr_pts   = max(0, (win_rate - 0.5) / 0.5) * 40
    roi_pts  = min(1, max(0, avg_roi) / 0.5) * 30
    cons_pts = consistency * 20
    vol_pts  = min(1, total / 20) * 10
    trust    = wr_pts + roi_pts + cons_pts + vol_pts

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
        "category_wr":    cat_wr,   # {"sports": 0.72, "politics": None, ...}
    }


def _empty_history(name: str) -> dict:
    return {
        "name": name, "wins": 0, "losses": 0, "total": 0,
        "win_rate": 0.0, "avg_roi": 0.0, "total_pnl": 0.0,
        "total_invested": 0.0, "consistency": 0.5, "trust_score": 0.0,
        "category_wr": {},
    }


def fetch_all_histories(
    wallets: set[str],
    trader_map: dict[str, dict],
    use_cache: bool = True,
) -> dict[str, dict]:
    """Tüm wallet'lar için kapalı pozisyonları paralel çekip analiz eder."""
    tasks = [(w, use_cache) for w in wallets]
    results: dict[str, dict] = {}

    closed_by_wallet: dict[str, list] = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(_fetch_closed_one, t): t[0] for t in tasks}
        for fut in as_completed(futures):
            w, closed = fut.result()
            closed_by_wallet[w] = closed

    for w, closed in closed_by_wallet.items():
        info = trader_map.get(w, {})
        name = info.get("name", w[:16] + "...")
        hist = analyze_trader_history(w, name, closed)
        results[w] = hist
        if hist["total"] > 0:
            cat_summary = "  ".join(
                f"{c}={v:.0%}" for c, v in hist["category_wr"].items() if v is not None
            )
            print(
                f"  {name:22}  WR={hist['win_rate']:.0%}  "
                f"avgROI={hist['avg_roi']:+.0%}  "
                f"({hist['wins']}W/{hist['losses']}L)  "
                f"güven={hist['trust_score']:.0f}/100"
                + (f"  [{cat_summary}]" if cat_summary else "")
            )
        else:
            print(f"  {name:22}  kapalı pozisyon verisi yok")
    return results


# ─── Konsensüs: daha iyi skorlama + deadline/drift filtresi ─────────────────

def score_position_group(
    positions: list[dict],
    trader_map: dict[str, dict],
    histories: dict[str, dict],
) -> float:
    """
    Konsensüs skoru: trader sayısı × ortalama güven kalitesi.
    (Eski tasarım: ham dolar büyüklüğü dominanttı → kötü trader sinyali şişiriyordu.)
    """
    n = len(positions)
    trust_scores = []
    for p in positions:
        w    = p.get("_wallet", "")
        hist = histories.get(w)
        if hist and hist["total"] >= 3:
            trust_scores.append(hist["trust_score"])
        else:
            # Geçmişi olmayan trader → tarafsız 30 puan
            trust_scores.append(30.0)

    avg_trust = sum(trust_scores) / len(trust_scores) if trust_scores else 30.0
    pnl_bonus = max(0, sum(float(p.get("percentPnl", 0)) for p in positions) / n / 10)

    return n * avg_trust + pnl_bonus * 5


def _hours_to_deadline(end_date_str: str | None) -> float | None:
    """
    endDate string'ini saate çevirir. None → bilinmiyor.
    İki formatı destekler:
      "2026-04-05"              (open positions API)
      "2026-04-05T00:00:00Z"   (closed positions API)
    """
    if not end_date_str:
        return None
    try:
        s = str(end_date_str).strip()
        if "T" in s:
            # "2026-04-05T12:00:00Z" veya "2026-04-05T12:00:00.000Z"
            s = s.split(".")[0].rstrip("Z")
            end = datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%S")
        else:
            # "2026-04-05"
            end = datetime.datetime.strptime(s[:10], "%Y-%m-%d")
        end = end.replace(tzinfo=datetime.timezone.utc)
        now = datetime.datetime.now(datetime.timezone.utc)
        return round((end - now).total_seconds() / 3600, 1)
    except Exception:
        return None


def find_consensus(
    all_positions: list[dict],
    trader_map: dict[str, dict],
    histories: dict[str, dict],
    min_traders: int = 2,
    same_direction: bool = True,
    min_hours: float = DEFAULT_MIN_HOURS,
) -> list[dict]:
    groups: dict[tuple, list] = defaultdict(list)
    for p in all_positions:
        cid     = p.get("conditionId", "")
        outcome = p.get("outcome", "?")
        key     = (cid, outcome) if same_direction else (cid,)
        groups[key].append(p)

    results = []
    for key, positions in groups.items():
        # Deduplicate: aynı wallet'tan tek pozisyon
        seen: set[str] = set()
        unique = []
        for p in positions:
            w = p.get("_wallet", "")
            if w not in seen:
                seen.add(w)
                unique.append(p)

        if len(unique) < min_traders:
            continue

        # Deadline filtresi — endDate açık pozisyonlarda zaten var
        end_date   = unique[0].get("endDate")
        hours_left = _hours_to_deadline(end_date)
        # hours_left < 0  → deadline geçmiş (açık pozisyon filtresi kaçırdıysa güvenlik)
        # hours_left < min_hours → çok yakında kapanıyor
        if hours_left is not None and hours_left < min_hours:
            continue

        # Market kategorisi
        title    = unique[0].get("title", "")
        category = classify_market(title)

        # Price drift: giriş fiyatından ne kadar uzaklaşmış?
        avg_entry   = sum(float(p.get("avgPrice", 0)) for p in unique) / len(unique)
        avg_current = sum(float(p.get("currentPrice", p.get("avgPrice", 0)))
                          for p in unique) / len(unique)
        price_drift = round(avg_current - avg_entry, 4)

        score = score_position_group(unique, trader_map, histories)
        results.append({
            "conditionId": key[0],
            "outcome":     key[1] if same_direction else unique[0].get("outcome", "?"),
            "positions":   unique,
            "n_traders":   len(unique),
            "score":       score,
            "category":    category,
            "hours_left":  hours_left,
            "price_drift": price_drift,
        })

    return sorted(results, key=lambda x: -x["score"])


# ─── Bet büyüklüğü ────────────────────────────────────────────────────────────

def suggest_bet(
    signal: dict,
    histories: dict[str, dict],
    bankroll: float,
    max_pct: float = DEFAULT_MAX_BET_PCT,
    kelly_frac: float = DEFAULT_KELLY_FRAC,
) -> dict:
    """
    half-Kelly tabanlı bet büyüklüğü.
    p_win hesabında kategori bazlı win rate varsa onu kullanır;
    yoksa genel win rate'e döner.
    """
    positions = signal["positions"]
    category  = signal.get("category", "other")
    prices    = [float(p.get("avgPrice", 0.5)) for p in positions]
    avg_price = sum(prices) / len(prices) if prices else 0.5

    # Trader'ların kategori bazlı güven skoru
    cat_wrs:   list[float] = []
    gen_trust: list[float] = []

    for p in positions:
        w    = p.get("_wallet", "")
        hist = histories.get(w)
        if not hist or hist["total"] < 3:
            gen_trust.append(30.0)
            continue
        # Önce bu kategorideki win rate'i tercih et
        wr_cat = hist.get("category_wr", {}).get(category)
        if wr_cat is not None:
            cat_wrs.append(wr_cat * 100)   # 0-100 skalasına çevir
        gen_trust.append(hist["trust_score"])

    # Ağırlıklı güven: kategori verisi varsa daha fazla ağırlık ver
    if cat_wrs:
        avg_trust = (sum(cat_wrs) / len(cat_wrs)) * 0.6 + (sum(gen_trust) / len(gen_trust)) * 0.4
    else:
        avg_trust = sum(gen_trust) / len(gen_trust) if gen_trust else 30.0

    # Konsensüs güçlendirmesi
    n_bonus   = (signal["n_traders"] - 2) * 5
    adj_trust = min(95, avg_trust + n_bonus)

    # Kazanma olasılığı: 0.50 base + trust katkısı (maks +0.30)
    p_win = 0.50 + (adj_trust / 100) * 0.30

    if avg_price <= 0 or avg_price >= 1:
        avg_price = 0.5
    b = (1.0 / avg_price) - 1.0

    kelly_f    = max(0, (b * p_win - (1 - p_win)) / b)
    bet_raw    = bankroll * kelly_f * kelly_frac
    bet_capped = min(bet_raw, bankroll * max_pct)
    edge_pct   = (p_win - avg_price) / avg_price * 100

    if kelly_f > 0 and edge_pct > 5:
        verdict = "✅ GİR"
        if signal["n_traders"] >= 4:
            verdict += " (güçlü konsensüs)"
    else:
        verdict = "⚠️  ZAYIF EDGE"

    return {
        "bet_usd":        round(bet_capped, 2),
        "confidence_pct": round(adj_trust, 1),
        "p_win":          round(p_win, 3),
        "edge_pct":       round(edge_pct, 1),
        "kelly_f":        round(kelly_f, 4),
        "note":           verdict,
        "category_used":  category,
    }


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
        drift   = c.get("price_drift", 0)
        hl      = c.get("hours_left")
        cat     = c.get("category", "other")
        deadline_str = f"{hl:.0f}s kaldı" if hl is not None else "deadline bilinmiyor"

        print(f"  #{i:02d}  [{outcome:12} @ {avg_p:.2f}]  "
              f"Skor:{c['score']:.0f}  [{cat}]  {deadline_str}")
        print(f"        {title}")
        print(f"        Toplam: ${total:>10,.0f}  |  Ort PnL: {pnl_avg:>+6.1f}%  |  "
              f"Fiyat kayması: {drift:+.3f}")

        for p in ps:
            w      = p.get("_wallet", "")
            trader = p.get("_trader", "?")
            cv     = float(p.get("currentValue", 0))
            ppnl   = float(p.get("percentPnl", 0))
            hist   = (histories or {}).get(w, {})
            ts     = hist.get("trust_score", 0)
            wr     = hist.get("win_rate", 0)
            n_tr   = hist.get("total", 0)
            roi    = hist.get("avg_roi", 0)
            cat_wr = hist.get("category_wr", {}).get(cat)

            hist_str = (
                f"güven={ts:.0f}/100  WR={wr:.0%}  "
                f"ROI={roi:+.0%}  ({n_tr}t)"
                + (f"  {cat}WR={cat_wr:.0%}" if cat_wr is not None else "")
            ) if n_tr > 0 else "geçmiş veri yok"

            print(f"          • {trader:22}  ${cv:>9,.0f}  {ppnl:>+6.1f}%   [{hist_str}]")

        if histories is not None:
            bet = suggest_bet(c, histories, bankroll)
            kazanirsa = bet["bet_usd"] / avg_p - bet["bet_usd"] if avg_p > 0 else 0
            print(
                f"        → {bet['note']}  |  "
                f"Güven:{bet['confidence_pct']:.0f}/100  "
                f"p_win:{bet['p_win']:.0%}  "
                f"Edge:{bet['edge_pct']:+.0f}%  |  "
                f"BET: ${bet['bet_usd']:.2f}  "
                f"(kazanırsa: +${kazanirsa:.2f})"
            )
        print()



# ─── Kaydetme ─────────────────────────────────────────────────────────────────

def save_signals(
    consensus: list[dict],
    traders_scanned: int,
    active_positions: int,
    output_path: str,
) -> None:
    output = {
        "generated_at":      datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "traders_scanned":   traders_scanned,
        "active_positions":  active_positions,
        "consensus_count":   len(consensus),
        "consensus_signals": [
            {
                "conditionId":    c["conditionId"],
                "title":          c["positions"][0].get("title", "?"),
                "outcome":        c["outcome"],
                "category":       c.get("category", "other"),
                "avg_price":      round(
                    sum(float(p.get("avgPrice", 0)) for p in c["positions"])
                    / len(c["positions"]), 4
                ),
                "n_traders":      c["n_traders"],
                "traders":        [p.get("_trader", "?") for p in c["positions"]],
                "total_value":    round(
                    sum(float(p.get("currentValue", 0)) for p in c["positions"]), 2
                ),
                "avg_pnl_pct":    round(
                    sum(float(p.get("percentPnl", 0)) for p in c["positions"])
                    / len(c["positions"]), 2
                ),
                "hours_left":     c.get("hours_left"),
                "price_drift":    c.get("price_drift", 0),
                "score":          round(c["score"], 2),
                "bet_suggestion": c.get("bet_suggestion", {}),
            }
            for c in consensus
        ],
    }

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)


# ─── Ana akış ────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="v2 Copy Trading Bot")
    parser.add_argument("--top-n",         type=int,   default=DEFAULT_TOP_N)
    parser.add_argument("--min-value",     type=float, default=DEFAULT_MIN_VALUE)
    parser.add_argument("--min-consensus", type=int,   default=DEFAULT_MIN_CONSENSUS)
    parser.add_argument("--period",        nargs="+",  default=DEFAULT_PERIODS,
                        choices=["week", "month", "all"])
    parser.add_argument("--same-direction", action="store_true", default=True)
    parser.add_argument("--output",        default=DEFAULT_OUTPUT)
    parser.add_argument("--bankroll",      type=float, default=DEFAULT_BANKROLL)
    parser.add_argument("--max-bet-pct",   type=float, default=DEFAULT_MAX_BET_PCT)
    parser.add_argument("--min-hours",     type=float, default=DEFAULT_MIN_HOURS,
                        help="Deadline'a en az N saat kalan marketler (varsayılan: 4)")
    parser.add_argument("--dry-run",       action="store_true")
    parser.add_argument("--no-save",       action="store_true")
    parser.add_argument("--skip-history",  action="store_true")
    parser.add_argument("--no-cache",      action="store_true",
                        help="Cache'i atla, her şeyi taze çek")
    args = parser.parse_args()

    use_cache = not args.no_cache
    now_str   = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print(f"\n{'═'*70}")
    print(f"  v2.1 — Polymarket Copy Trading Bot  ·  {now_str}")
    print(f"{'═'*70}\n")

    # 1. Top trader'lar
    print(f"[1/4] Top trader'lar çekiliyor  (dönem: {args.period}, n={args.top_n})...")
    trader_map     = fetch_top_traders(periods=args.period, top_n=args.top_n)
    traders_sorted = sorted(trader_map.values(), key=lambda t: -t["best_pnl"])
    print(f"  {len(trader_map)} unique trader\n")
    for t in traders_sorted[:10]:
        print(f"  {t['name']:22} ${t['best_pnl']:>11,.0f}  {t['period']}")
    if len(traders_sorted) > 10:
        print(f"  ... ve {len(traders_sorted)-10} trader daha")

    # 2. Açık pozisyonları paralel çek
    print(f"\n[2/4] Açık pozisyonlar çekiliyor  "
          f"(min_value=${args.min_value:,.0f}, paralel)...")
    all_positions = fetch_all_open_positions(traders_sorted, args.min_value, use_cache)
    active_traders = len({p["_wallet"] for p in all_positions})
    print(f"\n  Özet: {len(all_positions)} pozisyon / {active_traders} aktif trader")

    # 3. Kapalı pozisyon geçmişini paralel çek (konsensüs için gereken wallet'lar)
    histories: dict[str, dict] = {}
    if not args.skip_history:
        # Önce tüm wallet'ların geçmişini çek (consensus skoru için lazım)
        all_wallets = {p["_wallet"] for p in all_positions}
        print(f"\n[3/4] Trader güven analizi  ({len(all_wallets)} trader, paralel)...")
        histories = fetch_all_histories(all_wallets, trader_map, use_cache)
    else:
        print(f"\n[3/4] --skip-history aktif, atlandı.")

    # 4. Konsensüs bul (artık histories ile daha iyi skorlama)
    print(f"\n[4/4] Konsensüs analizi  "
          f"(min={args.min_consensus} trader, min_hours={args.min_hours})...")
    consensus = find_consensus(
        all_positions,
        trader_map,
        histories,
        min_traders=args.min_consensus,
        same_direction=args.same_direction,
        min_hours=args.min_hours,
    )

    # Rapor
    print(f"\n{'═'*70}")
    print(f"  KONSENSÜS SİNYALLER — {len(consensus)} market  (min {args.min_consensus} trader)")
    if not args.skip_history:
        print(f"  Bankroll: ${args.bankroll:,.0f}  |  Max tek bet: %{args.max_bet_pct*100:.0f}")
    print(f"{'═'*70}\n")

    print_consensus(
        consensus,
        histories=histories if not args.skip_history else None,
        bankroll=args.bankroll,
    )

    if len(consensus) == 0:
        print("  Konsensüs sinyal bulunamadı.")
        print("  İpucu: --min-consensus 2 veya --min-value 500 ile daha geniş tara.")

    # Kaydet
    if not args.no_save:
        enriched = []
        for c in consensus:
            bet = suggest_bet(c, histories, args.bankroll, args.max_bet_pct) if histories else {}
            enriched.append({**c, "bet_suggestion": bet})
        save_signals(enriched, len(trader_map), len(all_positions), args.output)
        print(f"\n  Kaydedildi → {args.output}  ({len(enriched)} konsensüs sinyal)")

    if args.dry_run:
        print(f"\n  [dry-run] CLOB emri gönderilmedi.")

    print(f"\n{'═'*70}\n")


if __name__ == "__main__":
    main()
