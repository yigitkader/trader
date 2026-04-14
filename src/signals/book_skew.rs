use crate::types::Features;

/// L2 bid ağırlığı: >0.5 → YES tarafına baskı (alış duvarı).
pub fn compute(f: &Features) -> f32 {
    let x = f.orderbook_imbalance.clamp(0.0, 1.0);
    ((x - 0.5) * 2.0).clamp(-1.0, 1.0)
}
