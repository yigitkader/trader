#!/usr/bin/env python3
"""
Run bundle içindeki signals_next*.jsonl dosyalarından "en iyi" policy önerisi çıkarır.

Amaç:
- Outcome beklemeden proxy hedef (forward_return_yes) ile:
  - exit horizon (steps) seç
  - dominant allowlist öner (pozitif net EV veren kovalar)

Net getiri (direction-adjusted):
  net = (BuyYes için fwd, BuyNo için -fwd) - cost

Kullanım:
  python3 scripts/tune_policy.py --run-dir runs/2026-04-14_205909 --cost 0.01
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


def mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else float("nan")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"{path.name} satır {i}: JSON hatası — {e}", file=sys.stderr)
    return out


def labeled_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in rows:
        lb = r.get("labels")
        if isinstance(lb, dict) and lb.get("forward_return_yes") is not None:
            if r.get("decision") in ("BuyYes", "BuyNo"):
                out.append(r)
    return out


def net_dir(r: dict[str, Any], cost: float) -> float:
    fr = float(r["labels"]["forward_return_yes"])
    if r.get("decision") == "BuyNo":
        fr = -fr
    return fr - cost


def analyze_file(path: Path, cost: float) -> dict[str, Any]:
    rows = load_jsonl(path)
    lab = labeled_rows(rows)
    nets = [net_dir(r, cost) for r in lab]
    by_dom: dict[str, list[float]] = defaultdict(list)
    for r in lab:
        by_dom[str(r.get("dominant_signal", "?"))].append(net_dir(r, cost))
    return {
        "file": str(path),
        "n": len(nets),
        "ev": mean(nets),
        "by_dom": {k: {"n": len(v), "ev": mean(v)} for k, v in by_dom.items()},
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Policy tuner (proxy forward_return)")
    ap.add_argument("--run-dir", type=Path, required=True)
    ap.add_argument("--cost", type=float, default=0.01, help="slippage+fee (örn 0.01=1%%)")
    ap.add_argument("--min-n", type=int, default=1000, help="min labeled sample for choosing best steps")
    ap.add_argument("--min-dom-n", type=int, default=500, help="min samples per dominant for allowlist decision")
    args = ap.parse_args()

    run_dir = args.run_dir
    if not run_dir.is_dir():
        print(f"run-dir yok: {run_dir}", file=sys.stderr)
        return 1

    pats = sorted(run_dir.glob("signals_next*.jsonl"))
    if not pats:
        print("signals_next*.jsonl yok.", file=sys.stderr)
        return 1

    # parse steps from filename
    step_re = re.compile(r"signals_next(\d+)\.jsonl$")
    candidates: list[tuple[int, Path]] = []
    for p in pats:
        m = step_re.search(p.name)
        if m:
            candidates.append((int(m.group(1)), p))

    if not candidates:
        print("signals_next{N}.jsonl bulunamadı.", file=sys.stderr)
        return 1

    cost = float(args.cost)
    analyses: list[tuple[int, dict[str, Any]]] = []
    for steps, path in sorted(candidates):
        a = analyze_file(path, cost)
        analyses.append((steps, a))

    # choose best steps by EV among sufficiently large samples
    eligible = [(s, a) for (s, a) in analyses if int(a["n"]) >= int(args.min_n)]
    if not eligible:
        eligible = analyses

    best_steps, best = max(eligible, key=lambda sa: float(sa[1]["ev"]))

    # dominant allowlist: pick those with positive EV and enough samples, ranked by EV
    dom_items = []
    for dom, v in best["by_dom"].items():
        if v["n"] >= args.min_dom_n:
            dom_items.append((dom, float(v["ev"]), int(v["n"])))
    dom_items.sort(key=lambda x: x[1], reverse=True)
    allow = [dom for dom, ev, n in dom_items if ev > 0.0]
    if not allow:
        # fallback: keep top-1 dominant with most samples
        allow = [max(dom_items, key=lambda x: x[2])[0]] if dom_items else ["FakeMove"]

    print(f"=== tune_policy ({run_dir}) ===")
    print(f"- cost: {cost:+.4f}")
    print(f"- considered: {len(analyses)} horizons")
    print("\nHorizons (steps -> n, EV):")
    for s, a in analyses:
        print(f"  next{s:<4} n={a['n']:<7} EV={a['ev']:+.5f}")
    print("\nBest horizon:")
    print(f"  POLYMARKET_EXIT_AFTER_OBS={best_steps}   (n={best['n']} EV={best['ev']:+.5f})")
    print("\nDominant EV (best horizon):")
    for dom, ev, n in dom_items:
        print(f"  {dom:12} n={n:<7} EV={ev:+.5f}")
    print("\nRecommended .env:")
    print(f"  POLYMARKET_EXIT_AFTER_OBS={best_steps}")
    print("  POLYMARKET_TRADE_DOMINANT_ALLOW=" + ",".join(allow))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

