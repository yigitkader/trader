use crate::types::Features;

// Ani spike: fiyat çok hızlı hareket etti
// + time decay yüksekse (kapanışa yakın) daha güçlü sinyal

const SPIKE_THRESHOLD: f32 = 0.06;  // %6 ani hareket = spike

pub fn compute(f: &Features) -> f32 {
    let is_spike = f.momentum.abs() > SPIKE_THRESHOLD;

    if is_spike {
        let spike_strength = (f.momentum.abs() / SPIKE_THRESHOLD).min(3.0) / 3.0;
        // kapanışa yakınsa amplify et
        let decay_amplifier = 1.0 + f.time_decay;
        (spike_strength * decay_amplifier).min(1.0)
    } else {
        0.0
    }
}