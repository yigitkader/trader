use crate::types::RawTrade;
use std::collections::VecDeque;

// Δprice / total_volume
// düşük  →  büyük hacim fiyatı az hareket ettirdi  →  güçlü karşı taraf var
// yüksek →  küçük hacim fiyatı çok hareket ettirdi →  zayıf likidite

pub fn compute(
    price_window: &VecDeque<(u64, f32)>,
    trades: &VecDeque<RawTrade>,
) -> f32 {
    if price_window.len() < 2 || trades.is_empty() {
        return 0.0;
    }

    let first = price_window.front().unwrap().1;
    let last = price_window.back().unwrap().1;
    let delta_price = (last - first).abs();

    let total_volume: f64 = trades.iter().map(|t| t.size).sum();

    if total_volume == 0.0 {
        return 0.0;
    }

    delta_price / total_volume as f32
}