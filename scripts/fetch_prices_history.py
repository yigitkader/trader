#!/usr/bin/env python3
"""
signals.jsonl'daki market_id'ler icin Polymarket CLOB API'den gercek fiyat gecmisini
indirir ve data/prices/<market_id>.json olarak kaydeder.

Akis:
  1. signals.jsonl'dan benzersiz market_id listesi cikar
  2. CLOB /markets/{condition_id} ile YES token_id al
  3. CLOB /prices-history?market=TOKEN&interval=max cek
  4. data/prices/<market_id>.json yaz

Kullanim:
  python3 scripts/fetch_prices_history.py
  python3 scripts/fetch_prices_history.py --input runs/2026-04-16_205945/signals.jsonl
  python3 scripts/fetch_prices_history.py --fidelity 5 --limit 50
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

CLOB_API = "https://clob.polymarket.com"

SLEEP_BETWEEN = 0.35  # saniye/istek (rate limit icin)
UA = "polymarket-trader-backtest/1.0"


def http_get(url: str, params: dict | None = None, timeout: int = 15) -> Any:
    """JSON donduruyor; HTTP hatasinda OSError firlatiyor."""
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise OSError(f"HTTP {e.code} — {url}") from e


def get_yes_token_id(condition_id: str) -> str | None:
    """CLOB /markets/{condition_id} -> YES token_id."""
    try:
        data = http_get(f"{CLOB_API}/markets/{condition_id}", timeout=10)
        tokens = data.get("tokens") or []
        for tok in tokens:
            if str(tok.get("outcome", "")).lower() == "yes":
                return str(tok["token_id"])
        if tokens:
            return str(tokens[0]["token_id"])
    except Exception as e:
        print(f"  token_id hata ({condition_id[:14]}): {e}", file=sys.stderr)
    return None


def get_prices_history(
    token_id: str,
    fidelity: int = 1,
    interval: str = "max",
) -> list[dict[str, Any]]:
    """CLOB /prices-history -> [{t, p}, ...] listesi."""
    try:
        data = http_get(
            f"{CLOB_API}/prices-history",
            params={"market": token_id, "interval": interval, "fidelity": fidelity},
            timeout=15,
        )
        return data.get("history") or []
    except Exception as e:
        print(f"  prices-history hata ({token_id[:14]}): {e}", file=sys.stderr)
    return []


def load_market_ids(path: Path) -> list[str]:
    seen: set[str] = set()
    ids: list[str] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                mid = d.get("market_id", "")
                if mid and mid not in seen:
                    seen.add(mid)
                    ids.append(mid)
            except json.JSONDecodeError:
                pass
    return ids


def main() -> int:
    ap = argparse.ArgumentParser(description="Polymarket gercek fiyat gecmisi indir")
    ap.add_argument("--input", type=Path, default=Path("signals.jsonl"),
                    help="Kaynak signals.jsonl (varsayilan: signals.jsonl)")
    ap.add_argument("--out-dir", type=Path, default=Path("data/prices"),
                    help="Cikti klasoru (varsayilan: data/prices)")
    ap.add_argument("--fidelity", type=int, default=1,
                    help="CLOB fidelity dakika (varsayilan: 1)")
    ap.add_argument("--interval", default="max",
                    choices=["max", "all", "1m", "1w", "1d", "6h", "1h"],
                    help="CLOB interval (varsayilan: max = tum gecmis)")
    ap.add_argument("--limit", type=int, default=0,
                    help="Kac market islensin (0 = hepsi)")
    ap.add_argument("--force", action="store_true",
                    help="Mevcut dosyalarin uzerine yaz")
    args = ap.parse_args()

    root = Path.cwd()
    inp = (root / args.input).resolve()
    if not inp.exists():
        runs = sorted((root / "runs").glob("*/signals.jsonl"), reverse=True)
        if runs:
            inp = runs[0]
            print(f"Input bulunamadi, en son run kullaniliyor: {inp}")
        else:
            print(f"Input yok: {args.input}", file=sys.stderr)
            return 1

    out_dir = (root / args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    market_ids = load_market_ids(inp)
    if args.limit and args.limit > 0:
        market_ids = market_ids[: args.limit]

    print(f"Kaynak  : {inp}")
    print(f"Cikti   : {out_dir}")
    print(f"Market  : {len(market_ids)} benzersiz")
    print(f"Interval: {args.interval}, fidelity: {args.fidelity}dk\n")

    ok = skipped = failed = 0

    for i, mid in enumerate(market_ids, 1):
        out_file = out_dir / f"{mid}.json"

        if not args.force and out_file.exists():
            skipped += 1
            continue

        print(f"[{i}/{len(market_ids)}] {mid[:18]}... ", end="", flush=True)

        # 1) token_id
        token_id = get_yes_token_id(mid)
        time.sleep(SLEEP_BETWEEN)
        if not token_id:
            print("token_id alinamadi")
            failed += 1
            continue

        # 2) fiyat gecmisi
        history = get_prices_history(token_id, args.fidelity, args.interval)
        time.sleep(SLEEP_BETWEEN)
        if not history:
            print("bos gecmis")
            failed += 1
            continue

        # 3) kaydet
        payload = {
            "market_id": mid,
            "token_id": token_id,
            "interval": args.interval,
            "fidelity_min": args.fidelity,
            "n_points": len(history),
            "history": history,
        }
        out_file.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")

        t_min = min(e["t"] for e in history)
        t_max = max(e["t"] for e in history)
        d_min = datetime.datetime.fromtimestamp(t_min, datetime.timezone.utc).strftime("%Y-%m-%d")
        d_max = datetime.datetime.fromtimestamp(t_max, datetime.timezone.utc).strftime("%Y-%m-%d")
        print(f"ok  {len(history)} nokta  {d_min}..{d_max}")
        ok += 1

        if i % 25 == 0:
            print(f"  -- ilerleme: {ok} ok / {failed} hata / {skipped} atlandi --")

    print(f"\nTamamlandi: {ok} indirildi, {skipped} atlandi (mevcut), {failed} hata")
    print(f"Klasor: {out_dir}")
    return 0 if (ok + skipped) > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
