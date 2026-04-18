#!/usr/bin/env python3
"""
fetch_prices_history.py ile indirilen GERCEK fiyat serisini kullanarak
signals.jsonl satirlarini etiketler.

Her satir icin:
  - labels.fwd_return_N  : signal zamanından N dakika sonraki fiyat degisimi
  - labels.outcome_yes   : fiyat kapanirken >0.95 oldu mu? (gercek kazanan tahmini)
  - labels.price_at_close: en son bilinen fiyat (kapanisa yakin son nokta)

Kullanim:
  python3 scripts/label_with_realprices.py
  python3 scripts/label_with_realprices.py --input runs/.../signals.jsonl --out labeled.jsonl
  python3 scripts/label_with_realprices.py --fwd-minutes 30,60,240,1440
"""

from __future__ import annotations

import argparse
import json
import sys
from bisect import bisect_left, bisect_right
from pathlib import Path
from typing import Any

OUTCOME_YES_THRESHOLD = 0.95   # fiyat bunu asarsa YES kazandi sayilir
OUTCOME_NO_THRESHOLD  = 0.05   # bunu altinda kalirsa NO kazandi sayilir


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"Satir {i} JSON hatasi: {e}", file=sys.stderr)
    return rows


def load_price_series(prices_dir: Path, market_id: str) -> tuple[list[int], list[float]] | None:
    """(timestamps, prices) tuple'i; yoksa None."""
    f = prices_dir / f"{market_id}.json"
    if not f.exists():
        return None
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
        history = d.get("history") or []
        if len(history) < 2:
            return None
        history_sorted = sorted(history, key=lambda x: x["t"])
        ts = [int(e["t"]) for e in history_sorted]
        ps = [float(e["p"]) for e in history_sorted]
        return ts, ps
    except Exception as e:
        print(f"  Fiyat serisi yuklenemiyor ({market_id[:12]}...): {e}", file=sys.stderr)
        return None


def price_at_time(ts: list[int], ps: list[float], target_ts: int) -> float | None:
    """Zaman serisinde target_ts anindaki en yakin fiyati don."""
    if not ts:
        return None
    idx = bisect_left(ts, target_ts)
    if idx == 0:
        return ps[0]
    if idx >= len(ts):
        return ps[-1]
    # target_ts'ye en yakin noktayi sec
    if abs(ts[idx] - target_ts) <= abs(ts[idx - 1] - target_ts):
        return ps[idx]
    return ps[idx - 1]


def price_after(ts: list[int], ps: list[float], signal_ts: int, delta_secs: int) -> float | None:
    """signal_ts + delta_secs zamanindaki fiyat; yoksa None."""
    target = signal_ts + delta_secs
    if target > ts[-1] + 300:   # 5dk tolerans
        return None
    return price_at_time(ts, ps, target)


def infer_outcome(ts: list[int], ps: list[float]) -> dict[str, Any]:
    """
    Son fiyat noktalarindan outcome_yes tahmin et.
    Son 3 noktanin ortalamasi kullanilir (ani spike'lardan korunmak icin).
    """
    if len(ps) < 1:
        return {}
    tail = ps[-3:] if len(ps) >= 3 else ps
    last_avg = sum(tail) / len(tail)
    last_price = ps[-1]

    result: dict[str, Any] = {
        "price_at_close": round(last_price, 6),
        "price_at_close_avg3": round(last_avg, 6),
    }
    if last_avg >= OUTCOME_YES_THRESHOLD:
        result["outcome_yes"] = 1
    elif last_avg <= OUTCOME_NO_THRESHOLD:
        result["outcome_yes"] = 0
    # Aralikta kalirsa outcome_yes eklenmez (belirsiz / henuz kapanmamis)
    return result


def label_row(
    row: dict[str, Any],
    ts: list[int],
    ps: list[float],
    fwd_minutes: list[int],
) -> dict[str, Any]:
    signal_ts = int(row.get("timestamp", 0))
    labels = row.get("labels") or {}
    if not isinstance(labels, dict):
        labels = {}

    # Forward return etiketleri
    p0 = row.get("tape_price") or row.get("price_at_signal")
    for m in fwd_minutes:
        p1 = price_after(ts, ps, signal_ts, m * 60)
        if p1 is not None and p0 and p0 > 0:
            fwd_ret = round((p1 - p0) / p0, 6)
            labels[f"fwd_return_{m}m"] = fwd_ret
            labels[f"price_fwd_{m}m"] = round(p1, 6)

    # Outcome tahmini
    outcome_info = infer_outcome(ts, ps)
    labels.update(outcome_info)

    new_row = dict(row)
    new_row["labels"] = labels
    return new_row


def main() -> int:
    ap = argparse.ArgumentParser(description="Gercek fiyat serisiyle signals.jsonl etiketle")
    ap.add_argument("--input", type=Path, default=None,
                    help="Kaynak signals.jsonl (varsayilan: en son runs/)")
    ap.add_argument("--prices-dir", type=Path, default=Path("data/prices"),
                    help="fetch_prices_history ciktisi (varsayilan: data/prices)")
    ap.add_argument("--out", type=Path, default=None,
                    help="Cikti JSONL (varsayilan: input yani _reallabeled.jsonl)")
    ap.add_argument("--fwd-minutes", default="30,60,240,1440",
                    help="Ileri donuslu dakikalar virgul ile (varsayilan: 30,60,240,1440)")
    args = ap.parse_args()

    root = Path.cwd()

    # Kaynak bul
    inp = args.input
    if inp is None:
        runs = sorted((root / "runs").glob("*/signals.jsonl"), reverse=True)
        if not runs:
            print("signals.jsonl bulunamadi. --input ile belirt.", file=sys.stderr)
            return 1
        inp = runs[0]
        print(f"En son run kullaniliyor: {inp}")
    else:
        inp = (root / inp).resolve()

    if not inp.exists():
        print(f"Dosya yok: {inp}", file=sys.stderr)
        return 1

    prices_dir = (root / args.prices_dir).resolve()
    if not prices_dir.exists():
        print(f"Fiyat dizini yok: {prices_dir}", file=sys.stderr)
        print("Once calistir: python3 scripts/fetch_prices_history.py", file=sys.stderr)
        return 1

    out = args.out
    if out is None:
        out = inp.parent / (inp.stem + "_reallabeled.jsonl")
    else:
        out = (root / out).resolve()

    fwd_minutes = []
    for part in str(args.fwd_minutes).split(","):
        part = part.strip()
        if part:
            try:
                fwd_minutes.append(int(part))
            except ValueError:
                print(f"Gecersiz fwd-minutes degeri: {part!r}", file=sys.stderr)
                return 1

    print(f"Kaynak : {inp}")
    print(f"Fiyat  : {prices_dir}")
    print(f"Cikti  : {out}")
    print(f"Forward: {fwd_minutes} dk\n")

    rows = load_jsonl(inp)
    print(f"{len(rows)} satir yuklendi")

    # Market basina fiyat serisi yukle
    unique_markets = list({r["market_id"] for r in rows if r.get("market_id")})
    series_cache: dict[str, tuple[list[int], list[float]] | None] = {}
    found = 0
    for mid in unique_markets:
        s = load_price_series(prices_dir, mid)
        series_cache[mid] = s
        if s is not None:
            found += 1
    print(f"{found}/{len(unique_markets)} market icin fiyat serisi bulundu")
    if found == 0:
        print("Hic fiyat serisi yok. Once fetch_prices_history.py calistir.", file=sys.stderr)
        return 1

    # Etiketle
    labeled = 0
    outcome_labeled = 0
    with out.open("w", encoding="utf-8") as f:
        for row in rows:
            mid = row.get("market_id", "")
            series = series_cache.get(mid)
            if series:
                row = label_row(row, series[0], series[1], fwd_minutes)
                labeled += 1
                if "outcome_yes" in (row.get("labels") or {}):
                    outcome_labeled += 1
            f.write(json.dumps(row, separators=(",", ":")) + "\n")

    print(f"\nSonuc:")
    print(f"  Fiyat serisi ile etiketlenen : {labeled}/{len(rows)} satir")
    print(f"  outcome_yes belirlenen        : {outcome_labeled} satir")
    print(f"  Cikti                         : {out}")
    if outcome_labeled > 10:
        print(f"\nSonraki adim:")
        print(f"  python3 scripts/calibrate_signals.py --file {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
