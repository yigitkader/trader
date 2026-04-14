pub mod momentum;
pub mod pressure;
pub mod reaction;

use crate::ingestion::book_feed::BookSnapshot;
use crate::strategy_params::StrategyParams;
use crate::types::{Features, Market, RawTrade};
use std::collections::VecDeque;

pub fn compute_all(
    market: &Market,
    price_window: &VecDeque<(u64, f32)>,
    trades: &VecDeque<RawTrade>,
    strategy: &StrategyParams,
    yes_book: Option<&BookSnapshot>,
) -> Features {
    let (imb, imb_w, spr) = match yes_book {
        Some(b) => (
            b.imbalance,
            b.imbalance_weighted,
            b.spread_abs.unwrap_or(0.0),
        ),
        None => (0.5, 0.5, 0.0),
    };

    Features {
        market_id: market.id.clone(),
        momentum: momentum::compute(price_window),
        pressure: pressure::compute(trades),
        reaction_speed: reaction::compute(price_window, trades),
        time_decay: compute_time_decay(market.time_to_resolution, strategy),
        trade_count: trades.len() as u32,
        orderbook_imbalance: imb.clamp(0.0, 1.0),
        orderbook_imbalance_weighted: imb_w.clamp(0.0, 1.0),
        orderbook_spread_l2: spr.max(0.0),
    }
}

/// Kapanışa yaklaştıkça 0 → 1; uçlarda üstel (kare) ağırlık — son saatlerde etki daha hızlı büyür.
fn compute_time_decay(ttr_secs: u64, strategy: &StrategyParams) -> f32 {
    let horizon = strategy.time_decay_horizon_hours;
    if horizon <= 0.0 || !horizon.is_finite() {
        return 0.0;
    }
    let hours = ttr_secs as f32 / 3600.0;
    if hours > horizon {
        return 0.0;
    }
    let ratio = (hours / horizon).clamp(0.0, 1.0);
    1.0 - ratio.powf(2.0)
}
