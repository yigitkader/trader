use crate::types::Market;

// Volume ve spread eşiklerini geç — sisteme alma
const MIN_VOLUME: f64 = 1000.0;
const MAX_SPREAD: f32 = 0.05;
const MIN_TTR_SECS: u64 = 3600; // en az 1 saat kalsın

pub fn is_tradeable(market: &Market) -> bool {
    if market.volume < MIN_VOLUME {
        return false;
    }
    if market.spread > MAX_SPREAD {
        return false;
    }
    if market.time_to_resolution < MIN_TTR_SECS {
        return false;
    }
    true
}