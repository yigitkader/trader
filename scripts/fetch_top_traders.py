#!/usr/bin/env python3
"""
Polymarket leaderboard'daki en karli traderlarin son 7 gunluk islemlerini ceker.

Akis:
  1. Bilinen wallet adreslerini yukle (leaderboard snapshot + auto-discovery)
  2. Her adres icin data-api.polymarket.com/activity cek (pagination ile)
  3. Son N gun filtrele
  4. data/top_traders/<address>/trades.json olarak kaydet

Kullanim:
  python3 scripts/fetch_top_traders.py
  python3 scripts/fetch_top_traders.py --days 7 --top 10
  python3 scripts/fetch_top_traders.py --add-address 0xABC...  # ekstra adres
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

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data" / "top_traders"
ACTIVITY_API = "https://data-api.polymarket.com/activity"
CLOB_API     = "https://clob.polymarket.com"

UA = "polymarket-trader-analysis/1.0"
SLEEP = 0.4   # rate limit


# ---------------------------------------------------------------------------
# Leaderboard'dan bilinen adresler (guncel snapshot — Nisan 2026)
# Kaynak: polymarket.com/leaderboard (Monthly, Profit/Loss sirali)
# Hex adresler leaderboard isimleriyle gorunuyor; adlar icin wallet discovery yapilir.
# ---------------------------------------------------------------------------

KNOWN_ADDRESSES: list[dict[str, str]] = [
    # Adres                                          Tahmini strateji
    {"addr": "0x492442EaB586F242B53bDa933fD5dE859c8A3782", "label": "bot_nba_top1"},
    {"addr": "0x2005d16a84ceefa912d4e380cd32e7ff827875ea", "label": "RN1_high_volume"},
    {"addr": "0x2a2C53bD278c04DA9962Fcf96490E17F3DfB9Bc1", "label": "bot_high_volume"},
]

# Isme gore arama: data-api'den profil slug denemesi
NAMED_USERS: list[str] = [
    "beachboy4", "sovereign2013", "elkmonkey", "swisstony",
    "gatorr", "texaskid", "denizz", "Dhdhsjsj",
]


def http_get(url: str, params: dict | None = None, timeout: int = 12) -> Any:
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise OSError(f"HTTP {e.code}") from e


def discover_address(username: str) -> str | None:
    """Kullanici adini wallet adresine cevir (polymarket.com profil sayfasi)."""
    # data-api.polymarket.com/profiles endpoint'leri deneniyor
    for url, params in [
        (f"https://data-api.polymarket.com/profiles", {"name": username}),
        (f"https://data-api.polymarket.com/profiles", {"pseudonym": username}),
        (f"https://data-api.polymarket.com/activity",  {"name": username, "limit": 1}),
    ]:
        try:
            data = http_get(url, params, timeout=8)
            if isinstance(data, list) and data:
                addr = data[0].get("proxyWallet") or data[0].get("address")
                if addr and addr.startswith("0x"):
                    return addr
            elif isinstance(data, dict):
                addr = data.get("proxyWallet") or data.get("address")
                if addr and addr.startswith("0x"):
                    return addr
        except Exception:
            pass
        time.sleep(SLEEP)
    return None


def fetch_activity(
    address: str,
    days: int = 7,
    max_pages: int = 20,
) -> list[dict[str, Any]]:
    """Son `days` gun icindeki tum TRADE aktivitelerini ceker."""
    cutoff_ts = int(
        (datetime.datetime.now(datetime.timezone.utc)
         - datetime.timedelta(days=days)).timestamp()
    )

    all_trades: list[dict[str, Any]] = []
    offset = 0
    limit  = 500

    for _ in range(max_pages):
        try:
            data = http_get(ACTIVITY_API, {
                "user": address.lower(),
                "limit": limit,
                "offset": offset,
            })
        except Exception as e:
            print(f"    API hata: {e}")
            break

        if not isinstance(data, list) or not data:
            break

        # Zaman filtresi
        page_trades = [
            d for d in data
            if d.get("type") == "TRADE"
            and int(d.get("timestamp", 0)) >= cutoff_ts
        ]
        all_trades.extend(page_trades)

        # Sayfanin en eskisi cutoff'tan once ise dur
        oldest_ts = min(int(d.get("timestamp", 0)) for d in data)
        if oldest_ts < cutoff_ts or len(data) < limit:
            break

        offset += limit
        time.sleep(SLEEP)

    return all_trades


def get_yes_token_id(condition_id: str) -> str | None:
    """CLOB /markets/{condition_id} -> YES token_id."""
    try:
        data = http_get(f"{CLOB_API}/markets/{condition_id}", timeout=8)
        for tok in data.get("tokens") or []:
            if str(tok.get("outcome", "")).lower() == "yes":
                return str(tok["token_id"])
        tokens = data.get("tokens") or []
        if tokens:
            return str(tokens[0]["token_id"])
    except Exception:
        pass
    return None


def enrich_trade(trade: dict[str, Any]) -> dict[str, Any]:
    """Trade kaydina yes_token_id ekle."""
    cid = trade.get("conditionId") or trade.get("condition_id")
    if cid and not trade.get("yes_token_id"):
        tid = get_yes_token_id(cid)
        trade["yes_token_id"] = tid
        time.sleep(SLEEP * 0.5)
    return trade


def main() -> int:
    ap = argparse.ArgumentParser(description="Top trader islemlerini indir")
    ap.add_argument("--days",         type=int, default=7,   help="Kac gunluk veri (varsayilan: 7)")
    ap.add_argument("--top",          type=int, default=10,  help="Kac trader (varsayilan: 10)")
    ap.add_argument("--add-address",  type=str, default="",  help="Ekstra wallet adresi")
    ap.add_argument("--discover-named", action="store_true", help="Isimli kullanicilarin adresini bulmaya calis")
    ap.add_argument("--skip-enrich",  action="store_true",   help="yes_token_id ekleme adimini atla")
    ap.add_argument("--force",        action="store_true",   help="Mevcut dosyalarin uzerine yaz")
    args = ap.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Adres listesini hazirla
    addresses = list(KNOWN_ADDRESSES)

    if args.add_address:
        addresses.insert(0, {"addr": args.add_address, "label": "custom"})

    if args.discover_named:
        print("Isimli kullanici adresleri aranıyor...")
        for username in NAMED_USERS[: args.top]:
            print(f"  {username}... ", end="", flush=True)
            addr = discover_address(username)
            if addr:
                addresses.append({"addr": addr, "label": f"named_{username}"})
                print(f"bulundu: {addr[:16]}...")
            else:
                print("bulunamadi")

    addresses = addresses[: args.top]

    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=args.days)
    print(f"\nHedef: {len(addresses)} trader | Son {args.days} gun ({cutoff.strftime('%Y-%m-%d')} itibaren)\n")

    total_trades = 0

    for info in addresses:
        addr  = info["addr"]
        label = info.get("label", addr[:10])
        out_dir  = DATA_DIR / addr.lower()
        out_file = out_dir / "trades.json"

        if not args.force and out_file.exists():
            existing = json.loads(out_file.read_text())
            print(f"  {label[:25]:25} {addr[:18]}  ATLA (mevcut: {len(existing.get('trades',[]))} trade)")
            continue

        print(f"  {label[:25]:25} {addr[:18]}  cekilior... ", end="", flush=True)

        trades = fetch_activity(addr, days=args.days)
        time.sleep(SLEEP)

        if not trades:
            print("0 trade")
            continue

        # YES/NO binary filtreleme + enrich
        binary_trades = [
            t for t in trades
            if str(t.get("outcome", "")).lower() in ("yes", "no")
        ]

        if not args.skip_enrich and binary_trades:
            # Her unique conditionId icin one kez token_id cek
            seen_cids: set[str] = set()
            cid_to_token: dict[str, str | None] = {}
            for t in binary_trades:
                cid = t.get("conditionId", "")
                if cid and cid not in seen_cids:
                    seen_cids.add(cid)
                    cid_to_token[cid] = get_yes_token_id(cid)
            for t in binary_trades:
                cid = t.get("conditionId", "")
                t["yes_token_id"] = cid_to_token.get(cid)

        # Istatistik
        usdc_total = sum(t.get("usdcSize", 0) for t in trades)
        binary_usdc = sum(t.get("usdcSize", 0) for t in binary_trades)

        result = {
            "address":      addr,
            "label":        label,
            "fetched_at":   datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "days":         args.days,
            "total_trades": len(trades),
            "binary_trades_yes_no": len(binary_trades),
            "total_usdc":   usdc_total,
            "binary_usdc":  binary_usdc,
            "trades":       trades,
        }

        out_dir.mkdir(parents=True, exist_ok=True)
        out_file.write_text(json.dumps(result, separators=(",", ":")), encoding="utf-8")
        total_trades += len(binary_trades)

        print(f"{len(trades):4} trade | binary YES/NO: {len(binary_trades):4} | USDC: ${usdc_total:>12,.0f}")

    print(f"\nToplam binary YES/NO trade: {total_trades}")
    print(f"Cikti: {DATA_DIR}")
    print("\nSonraki adim:")
    print("  python3 scripts/build_winner_dataset.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
