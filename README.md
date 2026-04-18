# Polymarket Trader Bot

Rust tabanlı otomatik Polymarket sinyal motoru.
Python pipeline ile haftalık backtest, top trader analizi ve `.env` otomatik tuning.

---

## Tam otomatik pipeline (önerilen kullanım)

```bash
# Her hafta veya yeni veri biriktikten sonra çalıştır:
python3 scripts/auto_tune.py --with-winners
```

Bu tek komut şu adımları sırayla çalıştırır:
1. `fetch_prices_history.py`  — CLOB'dan gerçek fiyat geçmişi indir
2. `label_with_realprices.py` — `signals.jsonl` satırlarını gerçek outcome ile etiketle
3. `fetch_top_traders.py`     — Leaderboard'daki top trader cüzdan adreslerinden son 7 günlük YES/NO işlemleri çek
4. `build_winner_dataset.py`  — Top trader işlemlerini market fiyat serisiyle birleştir, kazanan özellikleri çıkar
5. EV analizi + policy seçimi — Her `(dominant, karar)` kombinasyonu için EV hesapla
6. `.env` güncelle            — `SCORE_INVERT`, `TRADE_DOMINANT_ALLOW`, `MIN_EDGE`, `MIN/MAX_OUTCOME_MID`

---

## Bireysel adımlar

```bash
# Sadece fiyat geçmişi indir
python3 scripts/fetch_prices_history.py

# Sadece top trader verisi
python3 scripts/fetch_top_traders.py --days 7
python3 scripts/build_winner_dataset.py

# .env yazmadan analiz gör
python3 scripts/auto_tune.py --dry-run --skip-fetch

# Backtest başka bir run için
python3 scripts/auto_tune.py --input runs/2026-04-16_205945/signals.jsonl
```

---

## Güncel analiz sonuçları (Nisan 2026)

| Metrik | Değer |
|---|---|
| Bot sinyali doğru yön | ~26% (ham) → SCORE_INVERT=1 ile ~74% |
| FakeMove flip EV | +4.05% |
| Panic+BuyNo EV (inversiz) | +10.04% |
| Top trader win rate (mid fiyat) | %56.2 |
| Önerilen fiyat aralığı | 0.25 – 0.75 (mid) |

---

## Çalıştırma

```bash
cargo run          # bot başlat
cargo run -- --dry-run  # sinyal üret, emir gönderme
```

---

## Dosya yapısı

```
src/
  engine/mod.rs         — sinyal → karar
  engine/scorer.rs      — ham skor hesabı
  strategy_params.rs    — .env → StrategyParams
scripts/
  fetch_prices_history.py   — CLOB fiyat geçmişi
  fetch_top_traders.py      — Top trader işlem geçmişi
  build_winner_dataset.py   — Winner trade dataset
  label_with_realprices.py  — Gerçek outcome etiketi
  auto_tune.py              — Tam pipeline + .env güncelleme
data/
  prices/        — Market fiyat serileri (JSON)
  top_traders/   — Top trader aktiviteleri
  winner_trades.jsonl  — Birleştirilmiş kazanan trade dataset
runs/
  <tarih>/signals.jsonl        — Bot sinyal logu
  auto_tune_<tarih>.txt        — Tuning raporu
```
