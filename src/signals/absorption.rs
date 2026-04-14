use crate::features::effective_tape_momentum;
use crate::strategy_params::StrategyParams;
use crate::types::Features;

// Buyer-heavy tape + calm mid → seller absorbing (potential YES downside).
// Seller-heavy tape + calm mid → buyer absorbing (potential YES upside).
// Returns SIGNED value: positive → bullish YES, negative → bearish YES.

pub fn compute(f: &Features, strategy: &StrategyParams) -> f32 {
    if f.trade_count < strategy.absorption_min_trades {
        return 0.0;
    }

    let imbalance = f.pressure - 1.0;
    if imbalance.abs() < strategy.absorption_pressure_deadband {
        return 0.0;
    }

    let m = effective_tape_momentum(f).abs();
    if m >= strategy.absorption_max_momentum {
        return 0.0;
    }
    let calm = 1.0 - (m / strategy.absorption_max_momentum);
    let strength = (imbalance.abs() / strategy.absorption_pressure_scale).clamp(0.0, 1.0);

    // seller pressure (pressure < 1) + calm → buyers absorb → YES likely up → positive
    // buyer pressure (pressure > 1) + calm → sellers absorb → YES likely down → negative
    let direction = -imbalance.signum();
    (direction * strength * calm).clamp(-1.0, 1.0)
}
