#!/usr/bin/env python3
"""
signals.jsonl üzerinde özet: karar dağılımı, dominant_signal, ham sinyaller (log_schema≥1),
sentetik dominant (Rust motoru ile aynı mantık), özellik–edge Pearson, etiket özeti.

Kullanım:
  python3 scripts/calibrate_signals.py
  python3 scripts/calibrate_signals.py --file /path/to/signals.jsonl
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


def mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else float("nan")

def median(xs: list[float]) -> float:
    if not xs:
        return float("nan")
    ys = sorted(xs)
    n = len(ys)
    mid = n // 2
    if n % 2 == 1:
        return ys[mid]
    return 0.5 * (ys[mid - 1] + ys[mid])


def stdev(xs: list[float]) -> float:
    if len(xs) < 2:
        return float("nan")
    m = mean(xs)
    v = sum((x - m) ** 2 for x in xs) / (len(xs) - 1)
    return math.sqrt(v)


def pearson(a: list[float], b: list[float]) -> float | None:
    n = min(len(a), len(b))
    if n < 3:
        return None
    a, b = a[:n], b[:n]
    ma, mb = mean(a), mean(b)
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if va < 1e-18 or vb < 1e-18:
        return None
    cov = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    return cov / math.sqrt(va * vb)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
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


def synthetic_dominant(row: dict[str, Any]) -> str:
    """`engine/decision::dominant` ile aynı (fake/absorption/panic; book_skew dahil değil)."""
    ss = row.get("signal_snapshot") or {}
    dp = row.get("dominance_params") or {}
    af = abs(float(ss.get("fake_move", 0.0)))
    aa = abs(float(ss.get("absorption", 0.0)))
    ap = abs(float(ss.get("panic", 0.0)))
    max_v = max(af, aa, ap)
    mixed_max = float(dp.get("dominant_mixed_max", 0.05))
    eps = float(dp.get("dominant_tie_eps", 0.02))
    if max_v < mixed_max:
        return "Mixed"
    if abs(af - max_v) < eps and af >= aa and af >= ap:
        return "FakeMove"
    if abs(aa - max_v) < eps and aa >= ap:
        return "Absorption"
    if ap > 0.0:
        return "Panic"
    return "Mixed"


def main() -> int:
    ap = argparse.ArgumentParser(description="signals.jsonl kalibrasyon özeti")
    ap.add_argument(
        "--file",
        type=Path,
        default=Path("signals.jsonl"),
        help="JSONL dosyası (varsayılan: ./signals.jsonl)",
    )
    args = ap.parse_args()
    path: Path = args.file
    if not path.is_file():
        print(f"Dosya yok: {path}", file=sys.stderr)
        return 1

    rows = load_jsonl(path)
    if not rows:
        print("Kayıt yok.")
        return 0

    rows_v1 = [r for r in rows if int(r.get("log_schema", 0) or 0) >= 1]
    n_legacy = len(rows) - len(rows_v1)

    print(f"=== {path} — {len(rows)} sinyal (log_schema≥1: {len(rows_v1)}, eski şema: {n_legacy}) ===\n")

    by_decision: dict[str, int] = defaultdict(int)
    by_dom: dict[str, list[dict[str, Any]]] = defaultdict(list)
    edges: list[float] = []
    confs: list[float] = []
    moms: list[float] = []
    gtd: list[float] = []
    press: list[float] = []
    ob_imb: list[float] = []
    ob_w: list[float] = []
    tdecay: list[float] = []
    tcnt: list[float] = []

    for r in rows:
        d = r.get("decision", "?")
        by_decision[str(d)] += 1
        dom = str(r.get("dominant_signal", "?"))
        by_dom[dom].append(r)
        edges.append(float(r.get("edge_score", 0.0)))
        confs.append(float(r.get("confidence", 0.0)))
        fs = r.get("features_snapshot") or {}
        moms.append(float(fs.get("momentum", 0.0)))
        gtd.append(float(fs.get("gamma_tick_delta", 0.0)))
        press.append(float(fs.get("pressure", 0.0)))
        ob_imb.append(float(fs.get("orderbook_imbalance", 0.5)))
        ob_w.append(float(fs.get("orderbook_imbalance_weighted", 0.5)))
        tdecay.append(float(fs.get("time_decay", 0.0)))
        tcnt.append(float(fs.get("trade_count", 0.0)))

    print("Karar dağılımı (tüm satırlar):")
    for k, v in sorted(by_decision.items(), key=lambda x: -x[1]):
        print(f"  {k:12} {v:5} ({100.0 * v / len(rows):.1f}%)")
    print()

    print("dominant_signal kovaları (loglanan etiket, tüm satırlar):")
    for dom in sorted(by_dom.keys(), key=lambda d: -len(by_dom[d])):
        grp = by_dom[dom]
        n = len(grp)
        e_mean = mean([float(x.get("edge_score", 0.0)) for x in grp])
        c_mean = mean([float(x.get("confidence", 0.0)) for x in grp])
        ann_mean = mean([abs(float(x.get("annualized_edge", 0.0))) for x in grp])
        print(f"  {dom:20} n={n:4}  edgē={e_mean:+.4f}  conf̄={c_mean:.3f}  |ann|̄={ann_mean:.2f}")
    dom_keys = set(by_dom.keys())
    if dom_keys == {"Mixed"}:
        print(
            "  → Hepsi Mixed: strength_max genelde dominant_mixed_max altında; "
            "ham sinyal satırına bakın veya POLYMARKET_DOMINANT_MIXED_MAX düşürün."
        )
    print()

    if not rows_v1:
        print("log_schema≥1 satır yok — botu yeni sürümle çalıştırınca ham sinyal bölümü dolacak.\n")
    else:
        print("--- Ham sinyal (log_schema ≥ 1) ---\n")
        syn_counts: dict[str, int] = defaultdict(int)
        mismatch = 0
        fake_l: list[float] = []
        abs_l: list[float] = []
        panic_l: list[float] = []
        skew_l: list[float] = []
        smax_l: list[float] = []
        edges_v1: list[float] = []
        for r in rows_v1:
            syn = synthetic_dominant(r)
            syn_counts[syn] += 1
            logged = str(r.get("dominant_signal", ""))
            if logged != syn:
                mismatch += 1
            ss = r.get("signal_snapshot") or {}
            fake_l.append(float(ss.get("fake_move", 0.0)))
            abs_l.append(float(ss.get("absorption", 0.0)))
            panic_l.append(float(ss.get("panic", 0.0)))
            skew_l.append(float(ss.get("book_skew", 0.0)))
            smax_l.append(float(ss.get("strength_max", 0.0)))
            edges_v1.append(float(r.get("edge_score", 0.0)))

        print("Sentetik dominant (satırdaki signal_snapshot + dominance_params ile):")
        for k, v in sorted(syn_counts.items(), key=lambda x: -x[1]):
            print(f"  {k:20} n={v:5}")
        print(f"  Logged ≠ sentetik: {mismatch} / {len(rows_v1)}")
        print()

        print("Ham sinyal σ (yalnız log_schema≥1):")
        for name, xs in (
            ("fake_move", fake_l),
            ("absorption", abs_l),
            ("panic", panic_l),
            ("book_skew", skew_l),
            ("strength_max", smax_l),
        ):
            print(f"  σ({name:20}) = {stdev(xs):.6g}")
        print()

        print("edge_score ~ ham sinyal (Pearson, log_schema≥1):")
        for name, xs in (
            ("fake_move", fake_l),
            ("absorption", abs_l),
            ("panic", panic_l),
            ("book_skew", skew_l),
            ("strength_max", smax_l),
        ):
            pr = pearson(edges_v1, xs)
            rs = f"{pr:+.3f}" if pr is not None else "—"
            print(f"  edge ~ {name:20} r = {rs}")
        print()

    print("Özellik std sapması — tüm satırlar (~0 ise Pearson “—”):")
    feat_list = [
        ("confidence", confs),
        ("momentum", moms),
        ("gamma_tick_delta", gtd),
        ("pressure", press),
        ("orderbook_imbalance", ob_imb),
        ("orderbook_imbalance_weighted", ob_w),
        ("time_decay", tdecay),
        ("trade_count", tcnt),
    ]
    for name, xs in feat_list:
        print(f"  σ({name:32}) = {stdev(xs):.6g}")
    print()

    print("edge_score ~ özellikler (Pearson, tüm satırlar):")
    for name, xs in feat_list:
        r = pearson(edges, xs)
        rs = f"{r:+.3f}" if r is not None else "—"
        print(f"  edge ~ {name:32} r = {rs}")
    print()

    yes = [float(r["edge_score"]) for r in rows if r.get("decision") == "BuyYes"]
    no = [float(r["edge_score"]) for r in rows if r.get("decision") == "BuyNo"]
    if yes:
        print(f"BuyYes alt kümesi: n={len(yes)}  edgē={mean(yes):+.4f}")
    if no:
        print(f"BuyNo alt kümesi:  n={len(no)}  edgē={mean(no):+.4f}")
    print()

    idx = sorted(range(len(confs)), key=lambda i: confs[i])
    n3 = len(idx) // 3
    if n3 >= 1:
        low, mid, hi = idx[:n3], idx[n3 : 2 * n3], idx[2 * n3 :]
        print("Confidence üçte birlik dilimler — edgē:")
        for label, part in (("düşük", low), ("orta", mid), ("yüksek", hi)):
            if part:
                ee = [edges[i] for i in part]
                cc = [confs[i] for i in part]
                print(f"  {label:6} n={len(part):4}  conf̄={mean(cc):.3f}  edgē={mean(ee):+.4f}")
        print()

    # Etiketler (offline doldurulmuş outcome_yes / forward_return_yes)
    labeled_outcome = [
        r
        for r in rows
        if isinstance(r.get("labels"), dict) and r["labels"].get("outcome_yes") is not None
    ]
    labeled_fwd = [
        r
        for r in rows
        if isinstance(r.get("labels"), dict) and r["labels"].get("forward_return_yes") is not None
    ]
    print("--- Etiketler ---")
    if not labeled_outcome and not labeled_fwd:
        print("  (yok — çözüm sonrası outcome_yes veya scripts/label_forward_returns.py ile forward_return_yes doldurulabilir)\n")
    else:
        if labeled_outcome:
            print(f"  outcome_yes dolu: {len(labeled_outcome)} satır")
        if labeled_fwd:
            print(f"  forward_return_yes dolu: {len(labeled_fwd)} satır")

        def correct(r: dict[str, Any]) -> bool:
            oy = bool(r["labels"]["outcome_yes"])
            dec = r.get("decision")
            if dec == "BuyYes":
                return oy
            if dec == "BuyNo":
                return not oy
            return False

        hits = [correct(r) for r in labeled_outcome if r.get("decision") in ("BuyYes", "BuyNo")]
        if hits:
            rate = 100.0 * sum(1 for x in hits if x) / len(hits)
            print(f"  Yön isabeti (BuyYes→YES, BuyNo→NO): {rate:.1f}% (n={len(hits)})")
        fr = [float(r["labels"]["forward_return_yes"]) for r in labeled_fwd]
        if fr:
            print(f"  forward_return_yes: n={len(fr)}  ort={mean(fr):+.4f}")

            # Forward-return analizleri (proxy): decision yönüne göre ve dominant_signal kovalarına göre
            by_dec: dict[str, list[float]] = defaultdict(list)
            by_dom: dict[str, list[float]] = defaultdict(list)
            by_dom_signed: dict[str, list[float]] = defaultdict(list)

            for rr in labeled_fwd:
                dec = str(rr.get("decision", "?"))
                dom = str(rr.get("dominant_signal", "?"))
                ret = float(rr["labels"]["forward_return_yes"])
                by_dec[dec].append(ret)
                by_dom[dom].append(ret)
                # Yön-düzeltilmiş: BuyYes için ret, BuyNo için -ret
                if dec == "BuyYes":
                    by_dom_signed[dom].append(ret)
                elif dec == "BuyNo":
                    by_dom_signed[dom].append(-ret)

            print("  forward_return_yes by decision (ham):")
            for k in sorted(by_dec.keys(), key=lambda x: -len(by_dec[x])):
                xs = by_dec[k]
                print(f"    {k:10} n={len(xs):4}  ort={mean(xs):+.5f}  med={median(xs):+.5f}")

            print("  forward_return_yes by dominant_signal (ham):")
            for k in sorted(by_dom.keys(), key=lambda x: -len(by_dom[x])):
                xs = by_dom[k]
                print(f"    {k:12} n={len(xs):4}  ort={mean(xs):+.5f}  med={median(xs):+.5f}")

            if by_dom_signed:
                print("  forward_return_yes by dominant_signal (yön-düzeltilmiş):")
                for k in sorted(by_dom_signed.keys(), key=lambda x: -len(by_dom_signed[x])):
                    xs = by_dom_signed[k]
                    print(f"    {k:12} n={len(xs):4}  ort={mean(xs):+.5f}  med={median(xs):+.5f}")
        print()

    print(
        "Not: İsabet için outcome veya forward return şart; "
        "merge_outcome_labels.py ile CSV’den outcome_yes birleştirilebilir."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
