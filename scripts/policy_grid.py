#!/usr/bin/env python3
"""
signals*.jsonl üzerinde "policy grid" analizi:
- labels.forward_return_yes olan satırlarda net getiri = forward_return_yes - cost
- bucket'lara göre win rate ve EV (expected value) hesaplar

Örnek:
  python3 scripts/policy_grid.py --file runs/.../signals_next10.jsonl --cost 0.005

Notlar:
- forward_return_yes proxy hedeftir (çözüm doğruluğu değildir).
- cost sabit bir slippage+fee payıdır (örn. 0.5% = 0.005).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


def mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else float("nan")


def pct(xs: list[float], p: float) -> float:
    if not xs:
        return float("nan")
    ys = sorted(xs)
    k = (len(ys) - 1) * p
    i = int(math.floor(k))
    j = int(math.ceil(k))
    if i == j:
        return ys[i]
    return ys[i] * (j - k) + ys[j] * (k - i)


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
                print(f"satır {i}: JSON hatası — {e}", file=sys.stderr)
    return out


def iter_labeled(rows: Iterable[dict[str, Any]]) -> Iterable[dict[str, Any]]:
    for r in rows:
        lb = r.get("labels")
        if isinstance(lb, dict) and lb.get("forward_return_yes") is not None:
            yield r


def get_book_skew(r: dict[str, Any]) -> float:
    ss = r.get("signal_snapshot") or {}
    try:
        return float(ss.get("book_skew", 0.0))
    except Exception:
        return 0.0


def get_edge(r: dict[str, Any]) -> float:
    try:
        return float(r.get("edge_score", 0.0))
    except Exception:
        return 0.0


def get_fr(r: dict[str, Any]) -> float:
    return float(r["labels"]["forward_return_yes"])


def main() -> int:
    ap = argparse.ArgumentParser(description="Policy grid (win rate / EV) analizi")
    ap.add_argument("--file", type=Path, required=True, help="labels.forward_return_yes içeren JSONL")
    ap.add_argument("--cost", type=float, default=0.005, help="slippage+fee (örn 0.005=0.5%%)")
    ap.add_argument(
        "--edge-buckets",
        default="0.02,0.05,0.08",
        help="abs(edge) eşikleri (virgüllü)",
    )
    ap.add_argument(
        "--skew-buckets",
        default="0.6,0.7,0.8,0.9",
        help="abs(book_skew) eşikleri (virgüllü)",
    )
    args = ap.parse_args()

    if not args.file.is_file():
        print(f"Dosya yok: {args.file}", file=sys.stderr)
        return 1

    def parse_list(s: str) -> list[float]:
        xs: list[float] = []
        for part in s.split(","):
            part = part.strip()
            if not part:
                continue
            xs.append(float(part))
        return sorted(set(xs))

    edge_thr = parse_list(args.edge_buckets)
    skew_thr = parse_list(args.skew_buckets)

    rows = load_jsonl(args.file)
    labeled = list(iter_labeled(rows))
    if not labeled:
        print("forward_return_yes etiketli satır yok.", file=sys.stderr)
        return 1

    cost = float(args.cost)
    print(f"=== policy_grid: {args.file} ===")
    print(f"- labeled rows: {len(labeled)}")
    print(f"- cost: {cost:+.4f}\n")

    # Helper: compute net return with direction (BuyNo için işaret çevir)
    def net_dir(r: dict[str, Any]) -> float:
        fr = get_fr(r)
        dec = r.get("decision")
        if dec == "BuyNo":
            fr = -fr
        return fr - cost

    # Overall
    nets = [net_dir(r) for r in labeled if r.get("decision") in ("BuyYes", "BuyNo")]
    wins = [1.0 for x in nets if x > 0.0]
    print("Overall (direction-adjusted net):")
    print(f"  n={len(nets)}  EV={mean(nets):+.5f}  win%={(100.0*len(wins)/len(nets)):.1f}%  p5={pct(nets,0.05):+.5f}  p50={pct(nets,0.5):+.5f}  p95={pct(nets,0.95):+.5f}")
    print()

    # By dominant_signal
    by_dom: dict[str, list[float]] = defaultdict(list)
    for r in labeled:
        dec = r.get("decision")
        if dec not in ("BuyYes", "BuyNo"):
            continue
        dom = str(r.get("dominant_signal", "?"))
        by_dom[dom].append(net_dir(r))

    print("By dominant_signal (direction-adjusted net):")
    for dom in sorted(by_dom.keys(), key=lambda k: -len(by_dom[k])):
        xs = by_dom[dom]
        w = sum(1 for x in xs if x > 0.0)
        print(f"  {dom:12} n={len(xs):6}  EV={mean(xs):+.5f}  win%={100.0*w/len(xs):5.1f}%  p50={pct(xs,0.5):+.5f}")
    print()

    # Grid: edge bucket x skew bucket
    # Bucket definition: abs(edge)>=e and abs(skew)>=s
    grid: dict[tuple[float, float], list[float]] = defaultdict(list)
    for r in labeled:
        if r.get("decision") not in ("BuyYes", "BuyNo"):
            continue
        ae = abs(get_edge(r))
        asw = abs(get_book_skew(r))
        for e in edge_thr:
            if ae < e:
                continue
            for s in skew_thr:
                if asw < s:
                    continue
                grid[(e, s)].append(net_dir(r))

    print("Grid: abs(edge) >= e  AND  abs(book_skew) >= s (direction-adjusted net)")
    print("  (yalnız n>=200 satır gösterilir)")
    for (e, s), xs in sorted(grid.items(), key=lambda kv: (-len(kv[1]), -mean(kv[1]))):
        if len(xs) < 200:
            continue
        w = sum(1 for x in xs if x > 0.0)
        print(f"  e>={e:>5.3f}  s>={s:>3.1f}  n={len(xs):6}  EV={mean(xs):+.5f}  win%={100.0*w/len(xs):5.1f}%  p50={pct(xs,0.5):+.5f}")

    print("\nNot: Bu analiz proxy'dir; outcome_yes geldiğinde aynı grid outcome tabanlı yapılmalı.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

