pub mod momentum;
pub mod pressure;
pub mod reaction;

use crate::types::{Features, Market, RawTrade};
use std::collections::VecDeque;

pub fn compute_all(
    market: &Market,
    price_window: &VecDeque<(u64, f32)>,
    trades: &VecDeque<RawTrade>,
) -> Features {
    Features {
        market_id: market.id.clone(),
        momentum: momentum::compute(price_window),
        pressure: pressure::compute(trades),
        reaction_speed: reaction::compute(price_window, trades),
        time_decay: compute_time_decay(market.time_to_resolution),
        trade_count: trades.len() as u32,
    }
}

fn compute_time_decay(ttr_secs: u64) -> f32 {
    // kapanışa yaklaştıkça 0 → 1
    let hours = ttr_secs as f32 / 3600.0;
    if hours > 48.0 {
        return 0.0;
    }
    1.0 - (hours / 48.0)
}