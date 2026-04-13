use crate::types::Market;
use std::sync::OnceLock;

// Volume ve spread eşiklerini geç — sisteme alma
pub const MIN_VOLUME: f64 = 1000.0;
pub const MAX_SPREAD: f32 = 0.05;
pub const MIN_TTR_SECS: u64 = 3600; // en az 1 saat kalsın

/// Varsayılan üst süre: **24 saat** (sadece önümüzdeki 24 saat içinde çözülecek marketler).
pub const DEFAULT_MAX_TTR_SECS: u64 = 86_400;

/// Çok uzak vadeleri ele. `POLYMARKET_MAX_TTR_SECS` (saniye):
/// - tanımsız / boş → **86400** (24 saat)
/// - **0** → üst limit yok
/// - başka pozitif değer → o saniye
pub fn max_time_to_resolution_secs() -> Option<u64> {
    static MAX: OnceLock<Option<u64>> = OnceLock::new();
    MAX.get_or_init(|| {
        match std::env::var("POLYMARKET_MAX_TTR_SECS") {
            Err(_) => Some(DEFAULT_MAX_TTR_SECS),
            Ok(v) => {
                let t = v.trim();
                if t.is_empty() {
                    return Some(DEFAULT_MAX_TTR_SECS);
                }
                match t.parse::<u64>() {
                    Ok(0) => None,
                    Ok(n) => Some(n),
                    Err(_) => Some(DEFAULT_MAX_TTR_SECS),
                }
            }
        }
    })
    .clone()
}

#[derive(Debug, Default, Clone)]
pub struct FilterSummary {
    pub total: usize,
    pub tradeable: usize,
    /// `yes_token_id` dolu ve işlem gören
    pub tradeable_with_yes_token: usize,
    pub fail_time_to_resolution: usize,
    /// `POLYMARKET_MAX_TTR_SECS` ile çok uzak çözümlemeler
    pub fail_ttr_too_far: usize,
    pub fail_spread: usize,
    pub fail_volume: usize,
}

/// Neden çoğu piyasanın elendiğini görmek için (bir kayıt birden fazla sayaçta görünebilir).
pub fn filter_summary(markets: &[Market]) -> FilterSummary {
    let mut s = FilterSummary {
        total: markets.len(),
        ..Default::default()
    };
    for m in markets {
        if m.time_to_resolution < MIN_TTR_SECS {
            s.fail_time_to_resolution += 1;
        }
        if let Some(max) = max_time_to_resolution_secs() {
            if m.time_to_resolution > max {
                s.fail_ttr_too_far += 1;
            }
        }
        if m.spread > MAX_SPREAD {
            s.fail_spread += 1;
        }
        if let Some(v) = m.volume {
            if v < MIN_VOLUME {
                s.fail_volume += 1;
            }
        }
        if is_tradeable(m) {
            s.tradeable += 1;
            if m.yes_token_id.is_some() {
                s.tradeable_with_yes_token += 1;
            }
        }
    }
    s
}

pub fn is_tradeable(market: &Market) -> bool {
    if let Some(v) = market.volume {
        if v < MIN_VOLUME {
            return false;
        }
    }
    if market.spread > MAX_SPREAD {
        return false;
    }
    if market.time_to_resolution < MIN_TTR_SECS {
        return false;
    }
    if let Some(max) = max_time_to_resolution_secs() {
        if market.time_to_resolution > max {
            return false;
        }
    }
    true
}