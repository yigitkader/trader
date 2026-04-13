use crate::types::Features;

// Buyer-heavy tape + calm mid → seller absorbing (potential YES downside).
// Seller-heavy tape + calm mid → buyer absorbing (potential YES upside).
// Returns SIGNED value: positive → bullish YES, negative → bearish YES.

const MIN_TRADES: u32 = 6;
const MAX_MOMENTUM_FOR_ABSORPTION: f32 = 0.06;
const PRESSURE_SCALE: f32 = 0.35;

pub fn compute(f: &Features) -> f32 {
    if f.trade_count < MIN_TRADES {
        return 0.0;
    }

    let imbalance = f.pressure - 1.0;
    if imbalance.abs() < 0.01 {
        return 0.0;
    }

    let m = f.momentum.abs();
    if m >= MAX_MOMENTUM_FOR_ABSORPTION {
        return 0.0;
    }
    let calm = 1.0 - (m / MAX_MOMENTUM_FOR_ABSORPTION);
    let strength = (imbalance.abs() / PRESSURE_SCALE).clamp(0.0, 1.0);

    // seller pressure (pressure < 1) + calm → buyers absorb → YES likely up → positive
    // buyer pressure (pressure > 1) + calm → sellers absorb → YES likely down → negative
    let direction = -imbalance.signum();
    (direction * strength * calm).clamp(-1.0, 1.0)
}
