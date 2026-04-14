use crate::types::{Market, MultiArbKind, MultiOutcomeArbHint};
use chrono::{DateTime, NaiveDate, NaiveDateTime, Utc};
use reqwest::Client;
use serde::Deserialize;
use serde::Deserializer;

const GAMMA_API: &str = "https://gamma-api.polymarket.com";

/// Gamma bazen bu alanları **JSON string** olarak döndürüyor: `"[\"Yes\",\"No\"]"`.
fn deserialize_string_vec<'de, D>(deserializer: D) -> Result<Vec<String>, D::Error>
where
    D: Deserializer<'de>,
{
    use serde::de::Error;
    let v = serde_json::Value::deserialize(deserializer)?;
    match v {
        serde_json::Value::Null => Ok(Vec::new()),
        serde_json::Value::String(s) => {
            serde_json::from_str(&s).map_err(|e| Error::custom(format!("stringified json: {e}")))
        }
        serde_json::Value::Array(arr) => {
            let mut out = Vec::with_capacity(arr.len());
            for item in arr {
                match item {
                    serde_json::Value::String(s) => out.push(s),
                    serde_json::Value::Number(n) => out.push(n.to_string()),
                    serde_json::Value::Bool(b) => out.push(b.to_string()),
                    other => out.push(other.to_string()),
                }
            }
            Ok(out)
        }
        other => Err(Error::custom(format!(
            "expected string or array, got {}",
            other
        ))),
    }
}

/// Gamma listesi: hem hacim hem `endDateIso` hem de `clobTokenIds` burada.
#[derive(Deserialize)]
struct GammaMarket {
    #[serde(rename = "conditionId")]
    condition_id: String,
    #[serde(default)]
    question: Option<String>,
    #[serde(default, rename = "endDateIso")]
    end_date_iso: Option<String>,
    #[serde(default, rename = "volumeNum")]
    volume_num: Option<f64>,
    #[serde(default, rename = "spread")]
    spread: Option<f32>,
    #[serde(default, rename = "active")]
    active: Option<bool>,
    #[serde(default, rename = "closed")]
    closed: Option<bool>,
    #[serde(default, rename = "acceptingOrders")]
    accepting_orders: Option<bool>,
    #[serde(
        default,
        rename = "outcomes",
        deserialize_with = "deserialize_string_vec"
    )]
    outcomes: Vec<String>,
    #[serde(
        default,
        rename = "outcomePrices",
        deserialize_with = "deserialize_string_vec"
    )]
    outcome_prices: Vec<String>,
    /// outcome index ile paralel (çoğu market için).
    #[serde(
        default,
        rename = "clobTokenIds",
        deserialize_with = "deserialize_string_vec"
    )]
    clob_token_ids: Vec<String>,
}

/// İkili piyasalar + 3+ sonuçlu piyasalarda Σfiyat ≟ 1.0 arbitraj ipuçları.
pub async fn fetch_markets(
    client: &Client,
    multi_arb_sum_low: f32,
    multi_arb_sum_high: f32,
) -> anyhow::Result<(Vec<Market>, Vec<MultiOutcomeArbHint>)> {
    let mut markets = Vec::new();
    let mut multi_arbs = Vec::new();
    let mut offset: u32 = 0;
    const PAGE: u32 = 1000;

    loop {
        let url = format!("{}/markets?limit={}&offset={}", GAMMA_API, PAGE, offset);
        let resp = client
            .get(&url)
            .header(reqwest::header::USER_AGENT, "trader/0.1")
            .send()
            .await?;
        let status = resp.status();
        let body = resp.text().await?;
        if !status.is_success() {
            return Err(anyhow::anyhow!(
                "gamma http {}: {}",
                status,
                body.chars().take(400).collect::<String>()
            ));
        }

        let rows: Vec<GammaMarket> = serde_json::from_str(&body).map_err(|e| {
            anyhow::anyhow!(
                "gamma json parse error: {} | body_prefix={}",
                e,
                body.chars().take(400).collect::<String>()
            )
        })?;
        let got = rows.len();
        if rows.is_empty() {
            break;
        }

        for gm in rows {
            // Aktif + acceptingOrders + kapalı değil gibi temel filtreler (alanlar yoksa yine de düşmeyelim).
            if gm.closed.unwrap_or(false) {
                continue;
            }
            if gm.active == Some(false) {
                continue;
            }
            if gm.accepting_orders == Some(false) {
                continue;
            }

            // Çoklu sonuç (≥3): fiyat toplamı 1.0’dan sapınca ipucu (otomatik emir yok).
            if gm.outcomes.len() >= 3 && gm.outcomes.len() == gm.outcome_prices.len() {
                let mut prices = Vec::<f32>::new();
                let mut ok = true;
                for s in &gm.outcome_prices {
                    match s.parse::<f32>() {
                        Ok(p) if p.is_finite() && p >= 0.0 => prices.push(p),
                        _ => {
                            ok = false;
                            break;
                        }
                    }
                }
                if ok && prices.len() == gm.outcomes.len() {
                    let sum: f32 = prices.iter().sum();
                    let q = gm
                        .question
                        .as_deref()
                        .map(|x| x.trim().to_string())
                        .filter(|x| !x.is_empty())
                        .unwrap_or_else(|| "(soru yok)".to_string());
                    if sum < multi_arb_sum_low {
                        multi_arbs.push(MultiOutcomeArbHint {
                            condition_id: gm.condition_id.clone(),
                            question: q.clone(),
                            n_outcomes: gm.outcomes.len(),
                            sum_prices: sum,
                            kind: MultiArbKind::Underround,
                        });
                    } else if sum > multi_arb_sum_high {
                        multi_arbs.push(MultiOutcomeArbHint {
                            condition_id: gm.condition_id.clone(),
                            question: q,
                            n_outcomes: gm.outcomes.len(),
                            sum_prices: sum,
                            kind: MultiArbKind::Overround,
                        });
                    }
                }
            }

            // Sadece YES/NO marketleri al (mevcut pipeline yes/no varsayıyor).
            let yes_idx = gm.outcomes.iter().position(|o| o == "Yes");
            let no_idx = gm.outcomes.iter().position(|o| o == "No");
            let (Some(yi), Some(ni)) = (yes_idx, no_idx) else {
                continue;
            };

            let Some(yes_price) = gm
                .outcome_prices
                .get(yi)
                .and_then(|s| s.parse::<f32>().ok())
            else {
                continue;
            };
            let Some(no_price) = gm
                .outcome_prices
                .get(ni)
                .and_then(|s| s.parse::<f32>().ok())
            else {
                continue;
            };

            // Gamma bazen spread'i direkt veriyor; yoksa yaklaşık hesapla.
            let spread = gm
                .spread
                .unwrap_or_else(|| (yes_price + no_price - 1.0).abs());

            // WS aboneliği için YES token id (outcome index ile paralelse).
            let yes_token_id = gm
                .clob_token_ids
                .get(yi)
                .cloned()
                .filter(|s| !s.is_empty());
            let no_token_id = gm
                .clob_token_ids
                .get(ni)
                .cloned()
                .filter(|s| !s.is_empty());

            markets.push(Market {
                id: gm.condition_id,
                question: gm
                    .question
                    .map(|q| q.trim().to_string())
                    .filter(|q| !q.is_empty())
                    .unwrap_or_else(|| "(soru yok)".to_string()),
                yes_price,
                no_price,
                volume: gm.volume_num,
                spread,
                time_to_resolution: parse_ttr(gm.end_date_iso.as_deref().unwrap_or("")),
                market_type: crate::types::MarketType::Binary,
                yes_token_id,
                no_token_id,
            });
        }

        if got < PAGE as usize {
            break;
        }
        offset = offset.saturating_add(PAGE);
        if offset > 50_000 {
            break;
        }
    }

    Ok((markets, multi_arbs))
}

/// ISO 8601 bitiş zamanını UTC'ye çevirip şu ana göre kalan saniye (0 = geçmiş veya parse yok).
fn parse_ttr(iso: &str) -> u64 {
    let s = iso.trim();
    if s.is_empty() {
        return 0;
    }

    let Some(end_ts) = parse_iso_end_unix(s) else {
        return 0;
    };

    let now = crate::types::now_secs() as i64;
    let delta = end_ts.saturating_sub(now);
    if delta <= 0 {
        0
    } else {
        delta as u64
    }
}

fn parse_iso_end_unix(s: &str) -> Option<i64> {
    if let Ok(dt) = DateTime::parse_from_rfc3339(s) {
        return Some(dt.with_timezone(&Utc).timestamp());
    }

    if let Ok(dt) = s.parse::<DateTime<Utc>>() {
        return Some(dt.timestamp());
    }

    const NAIVE_FMT: &[&str] = &[
        "%Y-%m-%dT%H:%M:%S%.f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S%.f",
        "%Y-%m-%d %H:%M:%S",
    ];
    for fmt in NAIVE_FMT {
        if let Ok(naive) = NaiveDateTime::parse_from_str(s, fmt) {
            return Some(naive.and_utc().timestamp());
        }
    }

    if let Ok(date) = NaiveDate::parse_from_str(s, "%Y-%m-%d") {
        let naive = date.and_hms_opt(23, 59, 59)?;
        return Some(naive.and_utc().timestamp());
    }

    None
}
