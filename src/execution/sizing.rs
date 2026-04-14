//! Binary outcome için Kelly payı → emir büyüklüğü çarpanı (Half-Kelly ve üst sınır).

use rust_decimal::Decimal;

use crate::types::{Decision, Market};

/// Uzun taraf: maliyet `cost` iken kazanma olasılığı `p` (0..1). Pay ≈ (p−c)/(1−c).
pub fn kelly_fraction_long(p: f32, cost: f32) -> f32 {
    if !p.is_finite() || !cost.is_finite() {
        return 0.0;
    }
    if p <= cost || cost <= 0.01 || cost >= 0.99 {
        return 0.0;
    }
    (p - cost) / (1.0 - cost).clamp(0.02, 0.99)
}

/// `kelly_fraction` ile çarpılmış efektif Kelly; `target` civarında ~1x baz lot.
pub fn scaled_order_size(
    base: Decimal,
    decision: &Decision,
    confidence: f32,
    market: &Market,
    kelly_fraction: f32,
    kelly_target: f32,
    min_sz: Decimal,
    max_sz: Decimal,
) -> Decimal {
    let (p, cost) = match decision {
        Decision::BuyYes => (confidence, market.yes_price),
        Decision::BuyNo => ((1.0 - confidence).clamp(0.0, 1.0), market.no_price),
        Decision::Skip => return base.min(max_sz).max(min_sz),
    };

    let k_raw = kelly_fraction_long(p, cost) * kelly_fraction.clamp(0.0, 1.0);
    let target = kelly_target.max(0.005);
    let mult = (k_raw / target).clamp(0.2f32, 2.5f32);
    let m = Decimal::try_from(mult as f64).unwrap_or(Decimal::ONE);
    let mut sz = base * m;
    if sz < min_sz {
        sz = min_sz;
    }
    if sz > max_sz {
        sz = max_sz;
    }
    sz
}
