use crate::types::Features;

// Alıcı baskısı var ama fiyat artmıyor
// → güçlü satıcı absorbe ediyor
// → fiyat düşebilir

const PRESSURE_THRESHOLD: f32 = 1.3;   // buy/sell > 1.3 = alıcı baskısı
const MOMENTUM_WEAK: f32 = 0.01;       // ama fiyat bu kadar az hareket etti

pub fn compute(f: &Features) -> f32 {
    let buyer_pressure = f.pressure > PRESSURE_THRESHOLD;
    let price_not_moving = f.momentum.abs() < MOMENTUM_WEAK;

    if buyer_pressure && price_not_moving {
        let pressure_strength = ((f.pressure - PRESSURE_THRESHOLD) / PRESSURE_THRESHOLD).min(1.0);
        pressure_strength
    } else {
        0.0
    }
}