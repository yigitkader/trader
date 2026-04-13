use crate::types::Market;
use chrono::{DateTime, NaiveDate, NaiveDateTime, Utc};
use reqwest::Client;
use serde::Deserialize;

const API_BASE: &str = "https://clob.polymarket.com";

#[derive(Deserialize)]
struct ApiMarket {
    condition_id: String,
    question: String,
    tokens: Vec<ApiToken>,
    volume: f64,
    end_date_iso: String,
}

#[derive(Deserialize)]
struct ApiToken {
    outcome: String,
    price: f32,
}

pub async fn fetch_markets(client: &Client) -> anyhow::Result<Vec<Market>> {
    let url = format!("{}/markets", API_BASE);
    let response: serde_json::Value = client.get(&url).send().await?.json().await?;

    let mut markets = Vec::new();

    if let Some(arr) = response["data"].as_array() {
        for item in arr {
            let api: ApiMarket = serde_json::from_value(item.clone())?;

            let yes_price = api
                .tokens
                .iter()
                .find(|t| t.outcome == "Yes")
                .map(|t| t.price)
                .unwrap_or(0.5);

            let no_price = 1.0 - yes_price;
            let spread = (yes_price + no_price - 1.0).abs();

            markets.push(Market {
                id: api.condition_id,
                question: api.question,
                yes_price,
                no_price,
                volume: api.volume,
                spread,
                time_to_resolution: parse_ttr(&api.end_date_iso),
                market_type: crate::types::MarketType::Binary,
            });
        }
    }

    Ok(markets)
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
