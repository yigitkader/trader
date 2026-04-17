#!/usr/bin/env python3
"""
Tek komutla run paketi:
- signals.jsonl (ham) kopyala
- forward_return etiketli dosyaları üret (steps listesi)
- her üretilen dosya için calibrate_signals.py çalıştırıp özet yazdır

Çıktı klasörü:
  runs/YYYY-mm-dd_HHMMSS/
    signals.jsonl
    signals_next3.jsonl
    signals_next10.jsonl
    calibrate_next3.txt
    calibrate_next10.txt

Kullanım:
  python3 scripts/make_run_bundle.py
  python3 scripts/make_run_bundle.py --input signals.jsonl --steps 3,10,30
"""

from __future__ import annotations

import argparse
import datetime as dt
import shutil
import subprocess
import sys
from pathlib import Path


def run(cmd: list[str], cwd: Path) -> tuple[int, str]:
    p = subprocess.run(cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return p.returncode, p.stdout


def main() -> int:
    ap = argparse.ArgumentParser(description="Run bundle oluşturur (runs/ altında)")
    ap.add_argument("--input", type=Path, default=Path("signals.jsonl"))
    ap.add_argument("--runs-dir", type=Path, default=Path("runs"))
    ap.add_argument("--steps", default="10,30,60", help="virgülle steps listesi (varsayılan 10,30,60)")
    ap.add_argument("--policy-cost", type=float, default=0.01, help="policy_grid / tune_policy için cost (örn 0.01=1%%)")
    args = ap.parse_args()

    root = Path.cwd()
    inp = (root / args.input).resolve()
    if not inp.is_file():
        print(f"Input yok: {inp}", file=sys.stderr)
        return 1

    steps: list[int] = []
    for part in str(args.steps).split(","):
        part = part.strip()
        if not part:
            continue
        try:
            v = int(part)
        except ValueError:
            print(f"Geçersiz steps değeri: {part!r}", file=sys.stderr)
            return 1
        if v < 1:
            print(f"steps >= 1 olmalı: {v}", file=sys.stderr)
            return 1
        steps.append(v)
    if not steps:
        print("En az bir steps değeri gerekli.", file=sys.stderr)
        return 1

    ts = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out_dir = (root / args.runs_dir / ts)
    out_dir.mkdir(parents=True, exist_ok=False)

    # 1) ham log kopyala
    shutil.copy2(inp, out_dir / "signals.jsonl")

    print(f"Run bundle: {out_dir}")
    print(f"- copied: {inp.name} -> {out_dir/'signals.jsonl'}")

    # 2) forward-return label + calibrate
    for s in steps:
        out_jsonl = out_dir / f"signals_next{s}.jsonl"
        rc, out = run(
            ["python3", "scripts/label_forward_returns.py", "--input", str(inp), "--steps", str(s), "--output", str(out_jsonl)],
            cwd=root,
        )
        if rc != 0:
            print(out, file=sys.stderr)
            return rc
        print(f"- labeled: steps={s} -> {out_jsonl.name}")

        rc2, cal = run(
            ["python3", "scripts/calibrate_signals.py", "--file", str(out_jsonl)],
            cwd=root,
        )
        (out_dir / f"calibrate_next{s}.txt").write_text(cal, encoding="utf-8")
        if rc2 != 0:
            print(cal, file=sys.stderr)
            return rc2
        print(f"  - wrote: calibrate_next{s}.txt")

        # policy_grid çıktısı da kaydet (kâr policy tuning için)
        rc3, pol = run(
            ["python3", "scripts/policy_grid.py", "--file", str(out_jsonl), "--cost", str(args.policy_cost)],
            cwd=root,
        )
        (out_dir / f"policy_grid_next{s}.txt").write_text(pol, encoding="utf-8")
        if rc3 != 0:
            print(pol, file=sys.stderr)
            return rc3
        print(f"  - wrote: policy_grid_next{s}.txt")

    # tune_policy: en iyi horizon + dominant allowlist önerisi
    rc4, tune = run(
        ["python3", "scripts/tune_policy.py", "--run-dir", str(out_dir), "--cost", str(args.policy_cost)],
        cwd=root,
    )
    (out_dir / "policy_recommendation.txt").write_text(tune, encoding="utf-8")
    if rc4 != 0:
        print(tune, file=sys.stderr)
        return rc4
    print("  - wrote: policy_recommendation.txt")

    print("\nTamam. Sonuç dosyaları:")
    for p in sorted(out_dir.iterdir()):
        print(f"  - {p.relative_to(root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

