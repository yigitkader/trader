use crate::types::Features;

/// L2 bid ağırlığı: hacim + fiyat ağırlıklı karışım; >0.5 → YES tarafına baskı.
pub fn compute(f: &Features) -> f32 {
    let x = (0.55 * f.orderbook_imbalance + 0.45 * f.orderbook_imbalance_weighted).clamp(0.0, 1.0);
    ((x - 0.5) * 2.0).clamp(-1.0, 1.0)
}
