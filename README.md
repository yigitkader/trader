## Run bundle (önerilen)

Tek komutla `runs/` altında paket oluşturur, `signals_next{steps}.jsonl` üretir ve kalibrasyon çıktılarını kaydeder:

```bash
python3 scripts/make_run_bundle.py
# veya:
python3 scripts/make_run_bundle.py --steps 3,10,30
```

## Manuel (gerekirse)

```bash
python3 scripts/label_forward_returns.py --steps 3 --output signals_next3.jsonl
python3 scripts/label_forward_returns.py --steps 10 --output signals_next10.jsonl
python3 scripts/calibrate_signals.py --file signals_next3.jsonl
python3 scripts/calibrate_signals.py --file signals_next10.jsonl

python3 scripts/merge_outcome_labels.py --labels outcomes.csv --output signals_outcomes.jsonl
python3 scripts/calibrate_signals.py --file signals_outcomes.jsonl
```
