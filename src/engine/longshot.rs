//! Ucuz YES (long-shot) + kısa TTR: model skorunu NO yönüne çeker (maker NO limit ile uyumlu).

use crate::strategy_params::StrategyParams;
use crate::types::Market;

/// `raw_score` üzerine eklenecek delta (negatif → sigmoid düşer → BuyNo güçlenir).
pub fn raw_score_delta(market: &Market, strategy: &StrategyParams) -> f32 {
    if !strategy.longshot_enabled {
        return 0.0;
    }
    let y = market.yes_price;
    if y >= strategy.longshot_yes_max || y <= 0.001 {
        return 0.0;
    }
    if market.time_to_resolution > strategy.longshot_ttr_max_secs {
        return 0.0;
    }
    // YES ne kadar ucuzsa ve süre kısaysa o kadar güçlü NO eğimi
    let depth = (1.0 - y / strategy.longshot_yes_max).clamp(0.0, 1.0);
    let ttr_h = market.time_to_resolution as f32 / 3600.0;
    let urgency = (48.0 / ttr_h.max(0.5)).clamp(0.25, 4.0);
    -strategy.longshot_raw_weight * depth * urgency.sqrt()
}
