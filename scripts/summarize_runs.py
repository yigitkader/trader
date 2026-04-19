#!/usr/bin/env python3
"""
runs/auto_tune_*.txt raporlarından özet tablo üretir.

Kullanım:
  python3 scripts/summarize_runs.py
  python3 scripts/summarize_runs.py --runs-dir runs --limit 10
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TuneReport:
    filename: str
    date: str = "?"
    source: str = "?"
    normal_ev: str = "?"
    flip_ev: str = "?"
    score_invert: str = "?"
    dominant_allow: str = "?"
    min_edge: str = "?"
    winner_win_rate: str = "?"
    winner_pnl: str = "?"


RE_DATE          = re.compile(r"=== auto_tune raporu — (.+?) ===")
RE_SOURCE        = re.compile(r"^Kaynak:\s*(.+)")
RE_NORMAL_EV     = re.compile(r"^Normal EV toplam\s*:\s*([+-]?\d+\.\d+)")
RE_FLIP_EV       = re.compile(r"^Flip EV toplam\s*:\s*([+-]?\d+\.\d+)")
RE_INVERT        = re.compile(r"^\s*POLYMARKET_SCORE_INVERT=(\S+)")
RE_DOMINANT      = re.compile(r"^\s*POLYMARKET_TRADE_DOMINANT_ALLOW=(\S+)")
RE_EDGE          = re.compile(r"^\s*POLYMARKET_MIN_EDGE=(\S+)")
RE_WIN_RATE      = re.compile(r"Kazanma orani\s*:\s*([\d.]+%)")
RE_PNL           = re.compile(r"Toplam PnL\s*:\s*\$([^\s]+)")


def parse_report(path: Path) -> TuneReport:
    r = TuneReport(filename=path.name)
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = RE_DATE.search(line)
        if m:
            r.date = m.group(1)[:16]
            continue
        m = RE_SOURCE.match(line)
        if m:
            r.source = Path(m.group(1).strip()).parent.name
            continue
        m = RE_NORMAL_EV.match(line)
        if m:
            r.normal_ev = m.group(1)
            continue
        m = RE_FLIP_EV.match(line)
        if m:
            r.flip_ev = m.group(1)
            continue
        m = RE_INVERT.match(line)
        if m and r.score_invert == "?":
            r.score_invert = m.group(1).split("#")[0].strip()
            continue
        m = RE_DOMINANT.match(line)
        if m and r.dominant_allow == "?":
            r.dominant_allow = m.group(1)
            continue
        m = RE_EDGE.match(line)
        if m and r.min_edge == "?":
            r.min_edge = m.group(1)
            continue
        m = RE_WIN_RATE.search(line)
        if m and r.winner_win_rate == "?":
            r.winner_win_rate = m.group(1)
            continue
        m = RE_PNL.search(line)
        if m and r.winner_pnl == "?":
            r.winner_pnl = "$" + m.group(1)
            continue
    return r


def main() -> int:
    ap = argparse.ArgumentParser(description="auto_tune rapor ozeti")
    ap.add_argument("--runs-dir", type=Path, default=Path("runs"))
    ap.add_argument("--limit", type=int, default=20, help="kac rapor; 0 = hepsi")
    args = ap.parse_args()

    reports_dir: Path = args.runs_dir
    if not reports_dir.is_dir():
        raise SystemExit(f"runs-dir yok: {reports_dir}")

    files = sorted(reports_dir.glob("auto_tune_*.txt"), reverse=True)
    if args.limit:
        files = files[: args.limit]

    if not files:
        print("(auto_tune_*.txt raporu bulunamadi)")
        print("Calistir: python3 scripts/auto_tune.py")
        return 0

    recs = [parse_report(p) for p in files]

    cols   = ["date", "source", "normal_ev", "flip_ev", "score_invert", "dominant_allow", "min_edge", "winner_win%"]
    labels = ["tarih", "kaynak", "EV_normal", "EV_flip", "invert", "dominant", "min_edge", "winner_win%"]

    rows = [
        [r.date, r.source, r.normal_ev, r.flip_ev, r.score_invert,
         r.dominant_allow, r.min_edge, r.winner_win_rate]
        for r in recs
    ]

    widths = [
        max(len(labels[i]), *(len(row[i]) for row in rows))
        for i in range(len(labels))
    ]

    def fmt_row(cells: list[str]) -> str:
        return "  ".join(cells[i].ljust(widths[i]) for i in range(len(labels)))

    print(fmt_row(labels))
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print(fmt_row(row))

    print(f"\n{len(recs)} rapor listelendi.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
