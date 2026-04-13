use crate::types::Features;

// Monotonic stack mantığı:
// Fiyat hızlı yükseldi ama volume bunu desteklemiyor
// → sahte hareket, geri dönme ihtimali yüksek

const MOMENTUM_THRESHOLD: f32 = 0.03;  // %3 fiyat hareketi
const VOLUME_THRESHOLD: f32 = 0.001;   // düşük reaction speed eşiği

pub fn compute(f: &Features) -> f32 {
    let strong_momentum = f.momentum.abs() > MOMENTUM_THRESHOLD;
    let weak_volume = f.reaction_speed > VOLUME_THRESHOLD;

    if strong_momentum && weak_volume {
        // ne kadar güçlü sahte hareket?
        let momentum_strength = (f.momentum.abs() / MOMENTUM_THRESHOLD).min(3.0) / 3.0;
        let volume_weakness = (f.reaction_speed / VOLUME_THRESHOLD).min(3.0) / 3.0;
        (momentum_strength + volume_weakness) / 2.0
    } else {
        0.0
    }
}