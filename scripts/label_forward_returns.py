#!/usr/bin/env python3
"""
signals.jsonl → aynı market_id için ileri tarihli fiyatı bularak labels.forward_return_yes yaz.

Mantık:
- Her satır için `timestamp` ve `price_at_signal` (Gamma YES mid) alınır.
- Aynı market_id için `timestamp >= t0 + horizon_secs` olan ilk sonraki satır bulunur.
- forward_return_yes = (p1 - p0) / p0
- Çıktı yeni JSONL dosyasıdır (in-place edit yok).

Kullanım:
  # Zaman ufku (timestamp saniye):
  python3 scripts/label_forward_returns.py --horizon 300 --output signals_fwd300.jsonl

  # Dedup varsa daha pratik: bir sonraki gözleme göre (steps):
  python3 scripts/label_forward_returns.py --steps 1 --output signals_next.jsonl
  python3 scripts/label_forward_returns.py --steps 3 --output signals_next3.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from bisect import bisect_left
from collections import defaultdict
from pathlib import Path
from typing import Any


def load_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"satır {i}: JSON hatası — {e}", file=sys.stderr)
    return rows


def ensure_labels(obj: dict[str, Any]) -> dict[str, Any]:
    lb = obj.get("labels")
    if not isinstance(lb, dict):
        lb = {}
    if "schema_version" not in lb:
        lb["schema_version"] = 1
    return lb


def main() -> int:
    ap = argparse.ArgumentParser(description="signals.jsonl forward return etiketleme")
    ap.add_argument("--input", type=Path, default=Path("signals.jsonl"))
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument(
        "--horizon",
        type=int,
        default=None,
        help="ileri bakış saniye (timestamp saniye). Verilmezse --steps kullanılır.",
    )
    ap.add_argument(
        "--steps",
        type=int,
        default=1,
        help="aynı market_id için kaçıncı sonraki gözlem (dedup dostu, varsayılan 1)",
    )
    args = ap.parse_args()

    if not args.input.is_file():
        print(f"JSONL yok: {args.input}", file=sys.stderr)
        return 1

    rows = load_rows(args.input)
    if not rows:
        print("Kayıt yok.", file=sys.stderr)
        return 1

    use_steps = args.horizon is None

    # Per market: time series (timestamp[], price[]) — tape_price varsa onu kullan.
    per_market_ts: dict[str, list[int]] = defaultdict(list)
    per_market_p: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        mid = r.get("market_id")
        ts = int(r.get("timestamp", 0) or 0)
        p = float(r.get("tape_price", 0.0) or 0.0) or float(r.get("price_at_signal", 0.0) or 0.0)
        if isinstance(mid, str) and mid and ts > 0 and p > 0:
            per_market_ts[mid].append(ts)
            per_market_p[mid].append(p)

    # Horizon modunda timestamp sıralaması şart.
    if not use_steps:
        for mid, ts_list in list(per_market_ts.items()):
            paired = sorted(zip(ts_list, per_market_p[mid]))
            per_market_ts[mid] = [t for t, _ in paired]
            per_market_p[mid] = [p for _, p in paired]

    # Steps modunda her market_id için satır sırasındaki pozisyonu takip edeceğiz.
    cursors: dict[str, int] = defaultdict(int)

    n_labeled = 0
    n_total = 0
    with args.output.open("w", encoding="utf-8") as out:
        for r in rows:
            n_total += 1
            mid = r.get("market_id")
            ts0 = int(r.get("timestamp", 0) or 0)
            p0 = float(r.get("tape_price", 0.0) or 0.0) or float(r.get("price_at_signal", 0.0) or 0.0)
            if not (isinstance(mid, str) and mid and ts0 > 0 and p0 > 0):
                out.write(json.dumps(r, ensure_ascii=False) + "\n")
                continue

            p1 = None
            ts_list = per_market_ts.get(mid)
            if ts_list:
                if use_steps:
                    i0 = cursors[mid]
                    i1 = i0 + max(int(args.steps), 1)
                    cursors[mid] = i0 + 1
                    if i1 < len(ts_list):
                        p1 = per_market_p[mid][i1]
                else:
                    target = ts0 + int(args.horizon)
                    i = bisect_left(ts_list, target)
                    if i < len(ts_list):
                        p1 = per_market_p[mid][i]

            lb = ensure_labels(r)
            if p1 is not None and p0 > 1e-12:
                lb["forward_return_yes"] = (float(p1) - p0) / p0
                r["labels"] = lb
                n_labeled += 1

            out.write(json.dumps(r, ensure_ascii=False) + "\n")

    mode = f"steps={args.steps}" if use_steps else f"horizon={args.horizon}s"
    print(f"Ok: {n_total} satır işlendi; {n_labeled} satıra forward_return_yes yazıldı ({mode}) → {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

