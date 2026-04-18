## Tam otomatik backtest + .env güncelleme

Tek komutla:
1. Tüm marketler için CLOB'dan gerçek fiyat geçmişi indir
2. Gerçek outcome ile signals etiketle
3. Per-dominant EV hesapla
4. En karlı policy bul
5. `.env` otomatik güncelle

```bash
python3 scripts/auto_tune.py
```

Seçenekler:
```bash
python3 scripts/auto_tune.py --dry-run          # .env yazmadan analiz gör
python3 scripts/auto_tune.py --skip-fetch       # veri zaten varsa indirme adımını atla
python3 scripts/auto_tune.py --cost 0.02        # %2 komisyon varsayımı
python3 scripts/auto_tune.py --ev-min 0.03      # minimum EV eşiği
```

Rapor: `runs/auto_tune_<timestamp>.txt`

**Önerilen döngü:** Her yeni `signals.jsonl` birikimiyle:
```bash
cargo run                    # dry_run modunda veri topla
python3 scripts/auto_tune.py # backtest + .env güncelle
cargo run                    # yeni ayarlarla çalıştır
```

---

## Backtest (gerçek fiyat serisi ile — manuel)

```bash
# 1. Fiyat geçmişini indir
python3 scripts/fetch_prices_history.py

# 2. Gerçek outcome ile etiketle
python3 scripts/label_with_realprices.py

# 3. Kalibrasyon
python3 scripts/calibrate_signals.py --file runs/.../signals_reallabeled.jsonl
```

---

## Run bundle (proxy forward-return ile hızlı analiz)

```bash
python3 scripts/make_run_bundle.py --policy-cost 0.01
python3 scripts/summarize_runs.py --limit 0
```

