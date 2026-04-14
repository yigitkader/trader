#!/usr/bin/env python3
"""
Çözülmüş pazarlar için CSV → signals.jsonl içine labels.outcome_yes yazar.

CSV başlıklı (UTF-8):
  market_id,outcome_yes
  0xabc...,true
  0xdef...,0

outcome_yes: true/false/1/0/yes/no (büyük-küçük harf duyarsız)

Kullanım:
  python3 scripts/merge_outcome_labels.py --labels outcomes.csv --output signals_labeled.jsonl
  python3 scripts/merge_outcome_labels.py --labels outcomes.csv  # stdout
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, TextIO

LABEL_SCHEMA = 1


def parse_bool(raw: str) -> bool:
    t = raw.strip().lower()
    if t in ("1", "true", "yes", "y", "t"):
        return True
    if t in ("0", "false", "no", "n", "f"):
        return False
    raise ValueError(f"Geçersiz outcome_yes: {raw!r}")


def load_labels(path: Path) -> dict[str, bool]:
    out: dict[str, bool] = {}
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise SystemExit("CSV boş veya başlık yok")
        fn = [h.strip().lower() for h in reader.fieldnames]
        if "market_id" not in fn or "outcome_yes" not in fn:
            raise SystemExit(
                "CSV’de market_id ve outcome_yes sütunları gerekli "
                f"(bulunan: {reader.fieldnames})"
            )
        # Orijinal başlık adlarıyla oku
        id_key = reader.fieldnames[fn.index("market_id")]
        oy_key = reader.fieldnames[fn.index("outcome_yes")]
        for row in reader:
            mid = (row.get(id_key) or "").strip()
            if not mid:
                continue
            out[mid] = parse_bool(row[oy_key] or "")
    return out


def process_stream(fin: TextIO, fout: TextIO, labels: dict[str, bool]) -> tuple[int, int]:
    n_in, n_merged = 0, 0
    for line in fin:
        line = line.strip()
        if not line:
            continue
        n_in += 1
        obj: dict[str, Any] = json.loads(line)
        mid = obj.get("market_id")
        if mid in labels:
            lb = obj.get("labels")
            if not isinstance(lb, dict):
                lb = {}
            lb["schema_version"] = LABEL_SCHEMA
            lb["outcome_yes"] = labels[mid]
            obj["labels"] = lb
            n_merged += 1
        fout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    return n_in, n_merged


def main() -> int:
    ap = argparse.ArgumentParser(description="CSV outcome → signals.jsonl labels")
    ap.add_argument("--labels", type=Path, required=True, help="market_id,outcome_yes CSV")
    ap.add_argument("--input", type=Path, default=Path("signals.jsonl"))
    ap.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Yoksa stdout (pipe için)",
    )
    args = ap.parse_args()

    if not args.labels.is_file():
        print(f"CSV yok: {args.labels}", file=sys.stderr)
        return 1
    if not args.input.is_file():
        print(f"JSONL yok: {args.input}", file=sys.stderr)
        return 1

    try:
        lab = load_labels(args.labels)
    except (ValueError, OSError) as e:
        print(e, file=sys.stderr)
        return 1

    if args.output:
        with args.input.open(encoding="utf-8") as fin, args.output.open(
            "w", encoding="utf-8"
        ) as fout:
            n_in, n_merged = process_stream(fin, fout, lab)
        print(f"Ok: {n_in} satır okundu, {n_merged} satıra outcome_yes yazıldı → {args.output}")
    else:
        n_in, n_merged = process_stream(
            args.input.open(encoding="utf-8"), sys.stdout, lab
        )
        print(
            f"# merged {n_merged}/{n_in} rows",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
