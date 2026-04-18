#!/usr/bin/env python3
"""
Tam otomatik backtest + .env guncelleme pipeline.

Adimlar:
  1. fetch_prices_history.py  → data/prices/ altina gercek fiyat gecmisini indir
  2. label_with_realprices.py → signals_reallabeled.jsonl olustur
  3. Gercek outcome ile per-(dominant, direction) EV hesapla
  4. [Opsiyonel] winner_trades.jsonl varsa top trader market karakteristiklerini analiz et
  5. Optimal policy bul (en yuksek EV'li dominant + yon kombinasyonu)
  6. .env dosyasini guncelle (sadece policy satirlari; API anahtarlarina dokunma)
  7. Ozet raporu kaydet: runs/auto_tune_<ts>.txt

Kullanim:
  python3 scripts/auto_tune.py
  python3 scripts/auto_tune.py --input runs/2026-04-16_205945/signals.jsonl
  python3 scripts/auto_tune.py --dry-run       # .env yazma, sadece analiz goster
  python3 scripts/auto_tune.py --cost 0.02     # %2 komisyon varsayimi
  python3 scripts/auto_tune.py --with-winners  # fetch_top_traders + build_winner_dataset da calistir
"""

from __future__ import annotations

import argparse
import datetime
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

WINNER_FILE   = ROOT / "data" / "winner_trades.jsonl"

# EV esigi: bu degerden dusuk sinyalleri engelle
EV_MIN_THRESHOLD = 0.02   # %2 minimum EV gerekli

# .env'de guncellenmesine izin verilen satirlar (API keys vs dokunulmuyor)
PATCHABLE_KEYS = {
    "POLYMARKET_TRADE_DOMINANT_ALLOW",
    "POLYMARKET_EXIT_AFTER_OBS",
    "POLYMARKET_SCORE_INVERT",
    "POLYMARKET_MIN_EDGE",
    "POLYMARKET_EXIT_DIRECTION",
    "POLYMARKET_MIN_OUTCOME_MID",
    "POLYMARKET_MAX_OUTCOME_MID",
    "POLYMARKET_MAX_TTR_SECS",
}


# ---------------------------------------------------------------------------
# Yardimci: subprocess calistir
# ---------------------------------------------------------------------------

def run_step(cmd: list[str], label: str) -> tuple[bool, str]:
    print(f"\n[auto_tune] {label}...")
    result = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    combined = result.stdout + result.stderr
    if result.returncode != 0:
        print(f"  HATA (exit {result.returncode}):\n{combined[:800]}")
        return False, combined
    print(combined.strip()[:600] if combined.strip() else "  ok")
    return True, combined


# ---------------------------------------------------------------------------
# Veri yukleme
# ---------------------------------------------------------------------------

def load_labeled(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return rows


# ---------------------------------------------------------------------------
# Top trader winner analizi
# ---------------------------------------------------------------------------

def analyze_winner_trades(winner_file: Path, cost: float) -> dict[str, Any] | None:
    """
    winner_trades.jsonl'daki top trader islemlerini analiz eder.
    Kazanan trade'lerin market karakteristiklerini cikarir.
    Doner: market_filter onerileri (price_range, ttr_max, win_rate).
    """
    if not winner_file.exists():
        return None

    rows: list[dict[str, Any]] = []
    with winner_file.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

    labeled = [r for r in rows if r.get("won") is not None]
    if len(labeled) < 10:
        return None

    total     = len(labeled)
    won_rows  = [r for r in labeled if r.get("won") == 1]
    win_rate  = len(won_rows) / total

    total_pnl = sum(r.get("profit_usdc") or 0 for r in labeled)
    total_vol = sum(r.get("usdc_size") or 0 for r in labeled)

    # Kazanan trade'lerin fiyat dagilimi
    won_prices = [r["yes_price_at_entry"] for r in won_rows
                  if r.get("yes_price_at_entry") is not None]
    lost_prices = [r["yes_price_at_entry"] for r in labeled
                   if r.get("won") == 0 and r.get("yes_price_at_entry") is not None]

    def pct(lst: list[float], p: int) -> float:
        if not lst:
            return 0.5
        s = sorted(lst)
        idx = int(len(s) * p / 100)
        return s[min(idx, len(s)-1)]

    # Kazananlarin price level dagilimi
    price_buckets: dict[str, dict] = defaultdict(lambda: {"w": 0, "n": 0})
    for r in labeled:
        pl = r.get("market_features", {}).get("price_level", "mid")
        won = r.get("won", 0)
        price_buckets[pl]["n"] += 1
        price_buckets[pl]["w"] += int(won)

    best_price_level = max(
        price_buckets.items(),
        key=lambda x: (x[1]["w"] / max(x[1]["n"], 1))
    )[0] if price_buckets else "mid"

    # Fiyat araligini belirle (kazananlar penceresi)
    if won_prices:
        price_min = round(pct(won_prices, 10), 2)
        price_max = round(pct(won_prices, 90), 2)
    else:
        price_min, price_max = 0.05, 0.95

    return {
        "total_trades":     total,
        "win_rate":         round(win_rate, 4),
        "total_pnl":        round(total_pnl, 2),
        "total_volume":     round(total_vol, 2),
        "best_price_level": best_price_level,
        "recommended_price_min": max(0.03, price_min),
        "recommended_price_max": min(0.97, price_max),
        "price_buckets":    {k: {"n": v["n"], "win_rate": round(v["w"]/max(v["n"],1), 3)}
                             for k, v in price_buckets.items()},
        "won_price_p10":    round(pct(won_prices, 10), 3) if won_prices else None,
        "won_price_p90":    round(pct(won_prices, 90), 3) if won_prices else None,
    }


# ---------------------------------------------------------------------------
# EV hesabi
# ---------------------------------------------------------------------------

def compute_ev_table(rows: list[dict[str, Any]], cost: float) -> dict[tuple, dict]:
    """
    Her (dominant_signal, decision) cifti icin:
      - n      : islem sayisi
      - wins   : dogru yon tahmini
      - win_rate
      - avg_price : evet tarafinin ortalama fiyati
      - ev     : win_rate - bet_price - cost*bet_price
    """
    buckets: dict[tuple, dict] = defaultdict(
        lambda: {"wins": 0, "n": 0, "price_sum": 0.0}
    )

    for r in rows:
        dec = r.get("decision", "")
        dom = r.get("dominant_signal", "")
        lb  = r.get("labels", {})
        oy  = lb.get("outcome_yes")
        if oy is None or dec == "Skip" or not dom:
            continue

        yes_price = float(r.get("tape_price") or r.get("price_at_signal") or 0.5)
        win = (dec == "BuyYes" and oy == 1) or (dec == "BuyNo" and oy == 0)

        key = (dom, dec)
        buckets[key]["wins"] += int(win)
        buckets[key]["n"] += 1
        buckets[key]["price_sum"] += yes_price

    result = {}
    for (dom, dec), s in buckets.items():
        n = s["n"]
        if n == 0:
            continue
        wr = s["wins"] / n
        avg_yes = s["price_sum"] / n
        bet_price = avg_yes if dec == "BuyYes" else (1.0 - avg_yes)
        ev = wr - bet_price - cost * bet_price
        result[(dom, dec)] = {
            "n": n,
            "wins": s["wins"],
            "win_rate": wr,
            "avg_yes_price": avg_yes,
            "bet_price": bet_price,
            "ev": ev,
        }
    return result


def compute_flipped_ev(ev_table: dict[tuple, dict]) -> dict[tuple, float]:
    """
    Her (dom, dec) cifte ters yonde acilirsa EV ne olur?
    Ornegin FakeMove+BuyYes flip edilince FakeMove+BuyNo olur.
    win_rate_flip = 1 - win_rate, bet_price_flip = 1 - bet_price
    """
    flipped: dict[tuple, float] = {}
    for (dom, dec), s in ev_table.items():
        wr_flip = 1.0 - s["win_rate"]
        bp_flip = 1.0 - s["bet_price"]
        ev_flip = wr_flip - bp_flip - 0.01 * bp_flip  # cost approx
        flipped[(dom, dec)] = ev_flip
    return flipped


# ---------------------------------------------------------------------------
# Policy secimi
# ---------------------------------------------------------------------------

def select_policy(
    ev_table: dict[tuple, dict],
    flipped_evs: dict[tuple, float],
    cost: float,
    ev_min: float,
) -> dict[str, Any]:
    """
    Her dominant icin en iyi yonu bul:
      - Normal EV > ev_min  → o yonu trade et
      - Flipped EV > ev_min → INVERT modunda trade et
      - Ikisi de negatif     → bu dominanti engelle

    Doner:
      allowed_dominants : list[str]
      score_invert      : bool  (1=ters yon daha karli)
      min_edge          : float (kalibre edilmis)
      ev_by_dominant    : dict
    """
    dom_ev: dict[str, dict] = {}

    # Her dominant icin toplam beklenti: tum (dom, dec) cifti topla
    dom_agg: dict[str, dict] = defaultdict(
        lambda: {"ev_sum": 0.0, "n": 0, "ev_flip_sum": 0.0}
    )
    for (dom, dec), s in ev_table.items():
        dom_agg[dom]["ev_sum"] += s["ev"] * s["n"]
        dom_agg[dom]["ev_flip_sum"] += flipped_evs.get((dom, dec), 0.0) * s["n"]
        dom_agg[dom]["n"] += s["n"]

    normal_total_ev = sum(
        v["ev_sum"] for v in dom_agg.values()
    ) / max(sum(v["n"] for v in dom_agg.values()), 1)

    flip_total_ev = sum(
        v["ev_flip_sum"] for v in dom_agg.values()
    ) / max(sum(v["n"] for v in dom_agg.values()), 1)

    # Global: flip mi yoksa normal mi?
    score_invert = flip_total_ev > normal_total_ev

    allowed_dominants = []
    ev_by_dominant = {}

    for dom, agg in dom_agg.items():
        n = agg["n"]
        if n == 0:
            continue
        ev_normal = agg["ev_sum"] / n
        ev_flip   = agg["ev_flip_sum"] / n
        active_ev = ev_flip if score_invert else ev_normal

        ev_by_dominant[dom] = {
            "n": n,
            "ev_normal": ev_normal,
            "ev_flip":   ev_flip,
            "selected_ev": active_ev,
        }

        if active_ev >= ev_min:
            allowed_dominants.append(dom)

    # Min edge: ortalama pozitif EV'nin yarisini kullan (muhafazakar)
    positive_evs = [
        v["selected_ev"] for v in ev_by_dominant.values()
        if v["selected_ev"] > 0
    ]
    calibrated_min_edge = max(0.02, min(positive_evs) * 0.5) if positive_evs else 0.03

    return {
        "allowed_dominants": sorted(allowed_dominants),
        "score_invert": score_invert,
        "min_edge": round(calibrated_min_edge, 4),
        "normal_total_ev": normal_total_ev,
        "flip_total_ev": flip_total_ev,
        "ev_by_dominant": ev_by_dominant,
    }


# ---------------------------------------------------------------------------
# .env yamalama
# ---------------------------------------------------------------------------

def patch_env(env_path: Path, updates: dict[str, str]) -> bool:
    """Sadece PATCHABLE_KEYS satirlarini degistir; digerlerine dokunma."""
    if not env_path.exists():
        print(f"  .env bulunamadi: {env_path}")
        return False

    lines = env_path.read_text(encoding="utf-8").splitlines()
    patched: list[str] = []
    applied: set[str] = set()

    for line in lines:
        stripped = line.strip()
        matched_key = None
        for key in PATCHABLE_KEYS:
            if stripped.startswith(key + "=") or stripped.startswith("# " + key + "="):
                matched_key = key
                break

        if matched_key and matched_key in updates:
            patched.append(f"{matched_key}={updates[matched_key]}")
            applied.add(matched_key)
        else:
            patched.append(line)

    # Yeni satirlar (dosyada yoktu): politika blogunun sonuna ekle
    new_keys = set(updates.keys()) - applied
    if new_keys:
        patched.append("")
        patched.append("# --- auto_tune tarafindan eklendi ---")
        for k in sorted(new_keys):
            patched.append(f"{k}={updates[k]}")

    env_path.write_text("\n".join(patched) + "\n", encoding="utf-8")
    return True


# ---------------------------------------------------------------------------
# Rapor
# ---------------------------------------------------------------------------

def build_report(
    ev_table: dict[tuple, dict],
    flipped_evs: dict[tuple, float],
    policy: dict[str, Any],
    cost: float,
    labeled_path: Path,
    winner_analysis: dict[str, Any] | None = None,
) -> str:
    lines: list[str] = []
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines += [
        f"=== auto_tune raporu — {ts} ===",
        f"Kaynak: {labeled_path}",
        f"Maliyet varsayimi: %{cost*100:.1f}",
        "",
        "--- EV tablosu (dominant + karar) ---",
        f"{'dominant+karar':<30}  {'n':>6}  {'win%':>6}  {'price':>6}  {'EV':>8}  {'EV_flip':>8}",
        "-" * 78,
    ]
    for (dom, dec), s in sorted(ev_table.items(), key=lambda x: -x[1]["n"]):
        ev_f = flipped_evs.get((dom, dec), float("nan"))
        flag = " *** POZITIF" if s["ev"] > 0 else ("" if ev_f <= 0 else " (flip pozitif)")
        lines.append(
            f"{dom+'+'+dec:<30}  {s['n']:>6}  {s['win_rate']:>5.1%}  "
            f"{s['bet_price']:>6.3f}  {s['ev']:>+8.4f}  {ev_f:>+8.4f}{flag}"
        )

    lines += [
        "",
        "--- Dominant ozeti ---",
        f"{'dominant':<20}  {'n':>6}  {'EV normal':>10}  {'EV flip':>10}  {'aktif EV':>10}  secildi",
    ]
    for dom, info in sorted(policy["ev_by_dominant"].items(), key=lambda x: -x[1]["selected_ev"]):
        selected = "EVET" if dom in policy["allowed_dominants"] else "hayir"
        lines.append(
            f"{dom:<20}  {info['n']:>6}  {info['ev_normal']:>+10.4f}  "
            f"{info['ev_flip']:>+10.4f}  {info['selected_ev']:>+10.4f}  {selected}"
        )

    flip_str = "1 (TERS YON DAHA KARLI)" if policy["score_invert"] else "0 (NORMAL YON)"
    lines += [
        "",
        f"Normal EV toplam : {policy['normal_total_ev']:+.4f}",
        f"Flip EV toplam   : {policy['flip_total_ev']:+.4f}",
        "",
        "--- Onerilen .env guncelleme ---",
        f"  POLYMARKET_SCORE_INVERT={int(policy['score_invert'])}  # {flip_str}",
        f"  POLYMARKET_TRADE_DOMINANT_ALLOW={','.join(policy['allowed_dominants']) or 'none'}",
        f"  POLYMARKET_MIN_EDGE={policy['min_edge']}",
    ]

    if winner_analysis:
        wa = winner_analysis
        lines += [
            "",
            "--- Top trader market analizi ---",
            f"  Toplam trade    : {wa['total_trades']}",
            f"  Kazanma orani   : {wa['win_rate']:.1%}",
            f"  Toplam PnL      : ${wa['total_pnl']:,.0f}",
            f"  En iyi fiyat    : {wa['best_price_level']}",
        ]
        for pl, info in wa.get("price_buckets", {}).items():
            lines.append(f"    {pl:15s} n={info['n']:4d}  win={info['win_rate']:.1%}")
        lines += [
            "",
            "  Onerilen market filtresi (top trader kazanan fiyat penceresi):",
            f"    POLYMARKET_MIN_OUTCOME_MID={wa['recommended_price_min']}",
            f"    POLYMARKET_MAX_OUTCOME_MID={wa['recommended_price_max']}",
        ]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Ana akis
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Tam otomatik backtest + .env guncelleme")
    ap.add_argument("--input", type=Path, default=None,
                    help="Kaynak signals.jsonl (varsayilan: en son runs/)")
    ap.add_argument("--env", type=Path, default=Path(".env"),
                    help=".env dosyasi (varsayilan: .env)")
    ap.add_argument("--cost", type=float, default=0.01,
                    help="Komisyon orani (varsayilan: 0.01 = %%1)")
    ap.add_argument("--ev-min", type=float, default=EV_MIN_THRESHOLD,
                    help=f"Minimum EV esigi (varsayilan: {EV_MIN_THRESHOLD})")
    ap.add_argument("--dry-run", action="store_true",
                    help=".env yazma, sadece analiz goster")
    ap.add_argument("--skip-fetch", action="store_true",
                    help="fetch_prices_history adimini atla (data/prices/ zaten doluysa)")
    ap.add_argument("--skip-label", action="store_true",
                    help="label_with_realprices adimini atla (labeled dosya varsa)")
    ap.add_argument("--with-winners", action="store_true",
                    help="Top trader verisi de cek ve analiz et (fetch_top_traders + build_winner)")
    ap.add_argument("--skip-winners", action="store_true",
                    help="Winner analizi tamamen atla")
    args = ap.parse_args()

    # Kaynak bul
    inp: Path
    if args.input is None:
        runs = sorted((ROOT / "runs").glob("*/signals.jsonl"), reverse=True)
        if not runs:
            print("signals.jsonl bulunamadi. --input ile belirt.", file=sys.stderr)
            return 1
        inp = runs[0]
        print(f"En son run: {inp}")
    else:
        inp = (ROOT / args.input).resolve()

    if not inp.exists():
        print(f"Dosya yok: {inp}", file=sys.stderr)
        return 1

    labeled_path = inp.parent / (inp.stem + "_reallabeled.jsonl")
    prices_dir   = ROOT / "data" / "prices"
    env_path     = (ROOT / args.env).resolve()

    # --- Adim 1: Fiyat gecmisini indir ---
    if not args.skip_fetch:
        ok, _ = run_step(
            [sys.executable, "scripts/fetch_prices_history.py",
             "--input", str(inp), "--out-dir", str(prices_dir)],
            "Fiyat gecmisi indiriliyor (CLOB API)",
        )
        if not ok:
            print("fetch_prices_history basarisiz — devam ediyor (onceki data kullanilir)")

    # --- Adim 2: Etiketle ---
    if not args.skip_label or not labeled_path.exists():
        ok, _ = run_step(
            [sys.executable, "scripts/label_with_realprices.py",
             "--input", str(inp),
             "--prices-dir", str(prices_dir),
             "--out", str(labeled_path)],
            "Gercek outcome etiketi yaziliyor",
        )
        if not ok or not labeled_path.exists():
            print("label_with_realprices basarisiz.", file=sys.stderr)
            return 1
    else:
        print(f"\n[auto_tune] Label atlandi (mevcut: {labeled_path})")

    # --- Adim 2b: Top trader verisi (opsiyonel) ---
    if args.with_winners:
        run_step(
            [sys.executable, "scripts/fetch_top_traders.py", "--days", "7"],
            "Top trader trade'leri indiriliyor",
        )
        run_step(
            [sys.executable, "scripts/build_winner_dataset.py", "--days", "7"],
            "Winner dataset olusturuluyor",
        )

    # --- Adim 3: EV analizi ---
    print("\n[auto_tune] EV analizi yapiliyor...")
    rows = load_labeled(labeled_path)
    outcome_rows = [r for r in rows if r.get("labels", {}).get("outcome_yes") is not None
                    and r.get("decision", "Skip") != "Skip"]
    print(f"  outcome_yes dolu: {len(outcome_rows)} / {len(rows)} satir")

    if len(outcome_rows) < 50:
        print("  Yetersiz veri (< 50 outcome). Daha fazla data biriktir.", file=sys.stderr)
        return 1

    ev_table    = compute_ev_table(outcome_rows, args.cost)
    flipped_evs = compute_flipped_ev(ev_table)
    policy      = select_policy(ev_table, flipped_evs, args.cost, args.ev_min)

    # --- Adim 3b: Winner analizi ---
    winner_analysis: dict[str, Any] | None = None
    if not args.skip_winners and WINNER_FILE.exists():
        print("\n[auto_tune] Top trader winner analizi...")
        winner_analysis = analyze_winner_trades(WINNER_FILE, args.cost)
        if winner_analysis:
            print(f"  {winner_analysis['total_trades']} trade  "
                  f"win={winner_analysis['win_rate']:.1%}  "
                  f"PnL=${winner_analysis['total_pnl']:,.0f}")
        else:
            print("  Yetersiz winner data (atlandi)")

    # --- Adim 4: Rapor ---
    report = build_report(ev_table, flipped_evs, policy, args.cost, labeled_path, winner_analysis)
    print("\n" + report)

    ts = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    report_path = ROOT / "runs" / f"auto_tune_{ts}.txt"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    print(f"\nRapor kaydedildi: {report_path}")

    # --- Adim 5: .env guncelle ---
    if not policy["allowed_dominants"]:
        print("\nUYARI: Hicbir dominant pozitif EV gostermedi! .env guncellenmedi.")
        return 0

    env_updates: dict[str, str] = {
        "POLYMARKET_SCORE_INVERT":         str(int(policy["score_invert"])),
        "POLYMARKET_TRADE_DOMINANT_ALLOW": ",".join(policy["allowed_dominants"]),
        "POLYMARKET_MIN_EDGE":             str(policy["min_edge"]),
    }

    # Winner analizi varsa market filtrelerini guncelle
    if winner_analysis and winner_analysis.get("total_trades", 0) >= 30:
        env_updates["POLYMARKET_MIN_OUTCOME_MID"] = str(winner_analysis["recommended_price_min"])
        env_updates["POLYMARKET_MAX_OUTCOME_MID"] = str(winner_analysis["recommended_price_max"])

    if args.dry_run:
        print("\n[DRY RUN] .env yazilmadi. Onerilen degisiklikler:")
        for k, v in env_updates.items():
            print(f"  {k}={v}")
    else:
        if patch_env(env_path, env_updates):
            print(f"\n.env guncellendi: {env_path}")
            for k, v in env_updates.items():
                print(f"  {k}={v}")
        else:
            print("\n.env guncellenemedi.")
            return 1

    print("\n[auto_tune] Tamamlandi.")
    print("Bot'u calistirmak icin: cargo run")
    print("Sonraki otomatik tuning: python3 scripts/auto_tune.py --with-winners")
    return 0


if __name__ == "__main__":
    sys.exit(main())
