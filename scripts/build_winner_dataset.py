#!/usr/bin/env python3
"""
Top trader trade'lerini CLOB fiyat serisiyle birlestirir, outcome etiketler
ve hangi market/sinyal ozellikleri kazanciyla iliski oldugunu hesaplar.

Cikti: data/winner_trades.jsonl
Her satir bir trade:
  {
    "address", "label",
    "condition_id", "outcome_bought",   # Yes/No
    "price_entry", "usdc_size",
    "timestamp_entry",
    "yes_price_at_entry",               # fiyat serisinden interp
    "final_price",                      # son bilinen fiyat
    "outcome_yes",                      # 1/0 if resolved
    "profit_usdc",                      # tahmini kar
    "won",                              # 1/0
    "market_features": {
        "ttr_hours",                    # TTR at entry (tahmini)
        "price_level",                  # giren fiyat: cheap(<0.3)/mid/expensive(>0.7)
        "momentum_30m",                 # 30dk once ile fiyat farki
        "daily_price_range",            # gun ici max-min
    }
  }

Kullanim:
  python3 scripts/build_winner_dataset.py
  python3 scripts/build_winner_dataset.py --days 7
"""

from __future__ import annotations

import argparse
import datetime
import json
import math
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from bisect import bisect_left, bisect_right
from pathlib import Path
from typing import Any

ROOT       = Path(__file__).resolve().parent.parent
DATA_DIR   = ROOT / "data" / "top_traders"
PRICES_DIR = ROOT / "data" / "prices"
OUT_FILE   = ROOT / "data" / "winner_trades.jsonl"
CLOB_API   = "https://clob.polymarket.com"

UA    = "polymarket-trader-analysis/1.0"
SLEEP = 0.35

OUTCOME_YES_THRESH = 0.95
OUTCOME_NO_THRESH  = 0.05


def http_get(url: str, params: dict | None = None, timeout: int = 12) -> Any:
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise OSError(f"HTTP {e.code}") from e


# ---------------------------------------------------------------------------
# Fiyat serisi
# ---------------------------------------------------------------------------

def load_or_fetch_series(
    condition_id: str,
    yes_token_id: str | None,
) -> tuple[list[int], list[float]] | None:
    """data/prices/ den yukle; yoksa CLOB'dan cek."""
    cached = PRICES_DIR / f"{condition_id}.json"
    if cached.exists():
        d = json.loads(cached.read_text())
        hist = d.get("history") or []
        if len(hist) >= 2:
            hist = sorted(hist, key=lambda x: x["t"])
            return [e["t"] for e in hist], [float(e["p"]) for e in hist]

    if not yes_token_id:
        return None

    try:
        data = http_get(
            f"{CLOB_API}/prices-history",
            {"market": yes_token_id, "interval": "max", "fidelity": 1},
            timeout=15,
        )
        hist = data.get("history") or []
        if len(hist) < 2:
            return None
        hist = sorted(hist, key=lambda x: x["t"])
        # Opsiyonel: cache et
        PRICES_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "market_id": condition_id,
            "token_id": yes_token_id,
            "interval": "max",
            "fidelity_min": 1,
            "n_points": len(hist),
            "history": hist,
        }
        cached.write_text(json.dumps(payload, separators=(",", ":")))
        return [e["t"] for e in hist], [float(e["p"]) for e in hist]
    except Exception:
        time.sleep(SLEEP)
    return None


def price_at(ts: list[int], ps: list[float], target: int) -> float | None:
    if not ts:
        return None
    idx = bisect_left(ts, target)
    if idx == 0:
        return ps[0]
    if idx >= len(ts):
        return ps[-1]
    if abs(ts[idx] - target) <= abs(ts[idx - 1] - target):
        return ps[idx]
    return ps[idx - 1]


def infer_outcome(ts: list[int], ps: list[float]) -> int | None:
    """Son 3 noktanin ortalamasi: >0.95 → YES(1), <0.05 → NO(0), else None."""
    if not ps:
        return None
    tail = ps[-3:] if len(ps) >= 3 else ps
    avg = sum(tail) / len(tail)
    if avg >= OUTCOME_YES_THRESH:
        return 1
    if avg <= OUTCOME_NO_THRESH:
        return 0
    return None


def compute_features(
    ts: list[int],
    ps: list[float],
    entry_ts: int,
    yes_price_entry: float,
) -> dict[str, Any]:
    feats: dict[str, Any] = {}

    # 30dk onceki fiyat
    p30 = price_at(ts, ps, entry_ts - 1800)
    if p30 is not None and p30 > 0:
        feats["momentum_30m"] = round(yes_price_entry - p30, 5)
    else:
        feats["momentum_30m"] = None

    # 6 saatlik aralik (gun ici volatilite)
    window_lo = entry_ts - 21600
    window_hi = entry_ts + 3600
    in_window = [p for t, p in zip(ts, ps) if window_lo <= t <= window_hi]
    if in_window:
        feats["daily_price_range"] = round(max(in_window) - min(in_window), 4)
    else:
        feats["daily_price_range"] = None

    # Fiyat seviyesi kategorisi
    if yes_price_entry < 0.25:
        feats["price_level"] = "cheap_yes"
    elif yes_price_entry > 0.75:
        feats["price_level"] = "expensive_yes"
    else:
        feats["price_level"] = "mid"

    return feats


# ---------------------------------------------------------------------------
# Ana islem
# ---------------------------------------------------------------------------

def process_trader(
    trader_file: Path,
    days: int,
) -> list[dict[str, Any]]:
    data = json.loads(trader_file.read_text())
    trades = data.get("trades") or []
    address = data.get("address", "")
    label   = data.get("label", address[:10])

    cutoff_ts = int(
        (datetime.datetime.now(datetime.timezone.utc)
         - datetime.timedelta(days=days)).timestamp()
    )

    rows: list[dict[str, Any]] = []
    skipped_no_series = 0
    skipped_no_outcome = 0

    binary_trades = [
        t for t in trades
        if str(t.get("outcome", "")).lower() in ("yes", "no")
        and int(t.get("timestamp", 0)) >= cutoff_ts
    ]

    seen_cids: set[str] = set()

    for trade in binary_trades:
        cid        = trade.get("conditionId", "")
        tok_id     = trade.get("yes_token_id")
        entry_ts   = int(trade.get("timestamp", 0))
        price_paid = float(trade.get("price") or 0)
        usdc_size  = float(trade.get("usdcSize") or 0)
        outcome_b  = str(trade.get("outcome", "")).lower()  # "yes"/"no"

        if not cid or not price_paid:
            continue

        # Fiyat serisi
        series = load_or_fetch_series(cid, tok_id)
        if cid not in seen_cids and tok_id:
            seen_cids.add(cid)
            time.sleep(SLEEP * 0.7)

        if series is None:
            skipped_no_series += 1
            continue

        ts_list, ps_list = series

        # Giristeki YES fiyati
        yes_price_entry = price_at(ts_list, ps_list, entry_ts)
        if yes_price_entry is None:
            skipped_no_series += 1
            continue

        # Outcome
        outcome_yes = infer_outcome(ts_list, ps_list)
        final_price = ps_list[-1] if ps_list else None

        if outcome_yes is None:
            skipped_no_outcome += 1
            # Yine de ekle; outcome bos birakilir

        # Kar hesabi
        won: int | None = None
        profit_usdc = None
        if outcome_yes is not None and usdc_size > 0 and price_paid > 0:
            shares = usdc_size / price_paid
            if outcome_b == "yes":
                payout = shares * 1.0 if outcome_yes == 1 else 0.0
                profit_usdc = round(payout - usdc_size, 2)
                won = 1 if outcome_yes == 1 else 0
            else:  # bought NO
                no_price = 1.0 - price_paid
                no_shares = usdc_size / max(no_price, 0.01)
                payout = no_shares * 1.0 if outcome_yes == 0 else 0.0
                profit_usdc = round(payout - usdc_size, 2)
                won = 1 if outcome_yes == 0 else 0

        # Ozellikler
        feats = compute_features(ts_list, ps_list, entry_ts, yes_price_entry)

        rows.append({
            "address":           address,
            "label":             label,
            "condition_id":      cid,
            "title":             trade.get("title", ""),
            "outcome_bought":    outcome_b,
            "price_entry":       round(price_paid, 5),
            "yes_price_at_entry": round(yes_price_entry, 5),
            "usdc_size":         round(usdc_size, 2),
            "timestamp_entry":   entry_ts,
            "final_price":       round(final_price, 5) if final_price else None,
            "outcome_yes":       outcome_yes,
            "won":               won,
            "profit_usdc":       profit_usdc,
            "market_features":   feats,
        })

    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="Top trader trade'lerinden kazanan dataset olustur")
    ap.add_argument("--days",  type=int, default=7,  help="Kac gunluk veri (varsayilan: 7)")
    ap.add_argument("--force", action="store_true",  help="Mevcut winner_trades.jsonl'un uzerine yaz")
    args = ap.parse_args()

    if not DATA_DIR.exists():
        print(f"Top trader verisi yok: {DATA_DIR}")
        print("Once calistir: python3 scripts/fetch_top_traders.py")
        return 1

    trader_files = list(DATA_DIR.glob("*/trades.json"))
    if not trader_files:
        print("Hic trader dosyasi bulunamadi.")
        return 1

    print(f"{len(trader_files)} trader dosyasi isleniyor (son {args.days} gun)...\n")

    all_rows: list[dict[str, Any]] = []

    for tf in trader_files:
        label = tf.parent.name[:20]
        print(f"  {label}... ", end="", flush=True)
        rows = process_trader(tf, args.days)
        print(f"{len(rows)} satir (won={sum(1 for r in rows if r.get('won')==1)}, lost={sum(1 for r in rows if r.get('won')==0)})")
        all_rows.extend(rows)

    if not all_rows:
        print("Hic satir olusturulamadi.")
        return 1

    # Ozet istatistikler
    won_rows    = [r for r in all_rows if r.get("won") == 1]
    lost_rows   = [r for r in all_rows if r.get("won") == 0]
    labeled     = won_rows + lost_rows
    win_rate    = len(won_rows) / len(labeled) if labeled else 0
    total_pnl   = sum(r.get("profit_usdc") or 0 for r in labeled)

    print(f"\nToplam: {len(all_rows)} satir  |  outcome bilinen: {len(labeled)}")
    print(f"Kazanma orani: {win_rate:.1%}  |  Toplam PnL: ${total_pnl:,.0f}")

    # Market ozellikleri analizi
    if labeled:
        print("\n--- Market ozellikleri analizi ---")
        for pl in ["cheap_yes", "mid", "expensive_yes"]:
            subset = [r for r in labeled if r.get("market_features", {}).get("price_level") == pl]
            if subset:
                wr = sum(1 for r in subset if r.get("won") == 1) / len(subset)
                avg_p = sum(r["yes_price_at_entry"] for r in subset) / len(subset)
                print(f"  {pl:15s} n={len(subset):4d}  win={wr:.1%}  avg_price={avg_p:.3f}")

    # Kaydet
    if not args.force and OUT_FILE.exists():
        backup = OUT_FILE.with_suffix(".jsonl.bak")
        OUT_FILE.rename(backup)
        print(f"\nYedeklendi: {backup}")

    with OUT_FILE.open("w", encoding="utf-8") as f:
        for row in all_rows:
            f.write(json.dumps(row, separators=(",", ":")) + "\n")

    print(f"Kaydedildi: {OUT_FILE} ({len(all_rows)} satir)")
    print("\nSonraki adim:")
    print("  python3 scripts/auto_tune.py  (winner data'si da kullanilacak)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
