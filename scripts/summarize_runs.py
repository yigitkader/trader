#!/usr/bin/env python3
"""
runs/*/policy_recommendation.txt dosyalarından özet tablo üretir.

Çıktı: en yeni run en üstte olacak şekilde
- run_id
- cost
- best horizon (EXIT_AFTER_OBS)
- allowlist
- next10/30/60 EV'leri (varsa)

Kullanım:
  python3 scripts/summarize_runs.py
  python3 scripts/summarize_runs.py --runs-dir runs --limit 20
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Rec:
    run_id: str
    cost: str = "?"
    ev10: str = "?"
    ev30: str = "?"
    ev60: str = "?"
    best: str = "?"
    allow: str = "?"


RE_COST = re.compile(r"^- cost:\s*([+-]?\d+\.\d+)\s*$")
RE_HOR = re.compile(r"^\s*next(\d+)\s+n=(\d+)\s+EV=([+-]?\d+\.\d+)\s*$")
RE_BEST = re.compile(r"^\s*POLYMARKET_EXIT_AFTER_OBS=(\d+)\s+\(n=(\d+)\s+EV=([+-]?\d+\.\d+)\)\s*$")
RE_BEST_INLINE = re.compile(r"^\s*POLYMARKET_EXIT_AFTER_OBS=(\d+)\s+")
RE_ALLOW = re.compile(r"^\s*POLYMARKET_TRADE_DOMINANT_ALLOW=(.+?)\s*$")


def parse_policy_reco(path: Path) -> Rec:
    run_id = path.parent.name
    rec = Rec(run_id=run_id)
    txt = path.read_text(encoding="utf-8", errors="replace").splitlines()
    in_horiz = False
    in_env = False
    for line in txt:
        m = RE_COST.match(line)
        if m:
            rec.cost = m.group(1)
            continue
        if line.startswith("Horizons"):
            in_horiz = True
            in_env = False
            continue
        if line.startswith("Recommended .env"):
            in_env = True
            in_horiz = False
            continue
        if line.startswith("Best horizon"):
            # next line usually contains the env-style best horizon
            in_env = False
            in_horiz = False
            continue
        if in_horiz:
            mh = RE_HOR.match(line)
            if mh:
                k = mh.group(1)
                ev = mh.group(3)
                if k == "10":
                    rec.ev10 = ev
                elif k == "30":
                    rec.ev30 = ev
                elif k == "60":
                    rec.ev60 = ev
        if in_env:
            mb = RE_BEST.match(line.strip())
            if mb:
                rec.best = mb.group(1)
            else:
                mb2 = RE_BEST_INLINE.match(line.strip())
                if mb2:
                    rec.best = mb2.group(1)
            ma = RE_ALLOW.match(line.strip())
            if ma:
                rec.allow = ma.group(1)

        # Best horizon is printed outside Recommended .env in tune_policy output.
        mb3 = RE_BEST_INLINE.match(line.strip())
        if mb3 and rec.best == "?":
            rec.best = mb3.group(1)
    return rec


def main() -> int:
    ap = argparse.ArgumentParser(description="runs policy recommendation özeti")
    ap.add_argument("--runs-dir", type=Path, default=Path("runs"))
    ap.add_argument(
        "--limit",
        type=int,
        default=30,
        help="kaç run gösterilsin; 0 = hepsi",
    )
    args = ap.parse_args()

    runs_dir: Path = args.runs_dir
    if not runs_dir.is_dir():
        raise SystemExit(f"runs-dir yok: {runs_dir}")

    files = sorted(runs_dir.glob("*/policy_recommendation.txt"), reverse=True)
    if args.limit != 0:
        files = files[: max(args.limit, 1)]
    if not files:
        print("(policy_recommendation.txt bulunamadı)")
        return 0

    recs = [parse_policy_reco(p) for p in files]

    # Print table
    cols = ["run_id", "cost", "best", "allow", "ev10", "ev30", "ev60"]
    rows = [[getattr(r, c) for c in cols] for r in recs]
    widths = [max(len(c), *(len(row[i]) for row in rows)) for i, c in enumerate(cols)]

    def fmt_row(row: list[str]) -> str:
        return "  ".join(row[i].ljust(widths[i]) for i in range(len(cols)))

    print(fmt_row(cols))
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print(fmt_row(row))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

