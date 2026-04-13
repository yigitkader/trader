use crate::types::Features;

// Large spike: rapid momentum move. Near expiry amplified.
// SIGNED: spike UP → trend continuation → bullish YES → positive.
// Spike DOWN → trend continuation → bearish YES → negative.

const SPIKE_THRESHOLD: f32 = 0.06;

pub fn compute(f: &Features) -> f32 {
    let m = f.momentum;
    if m.abs() <= SPIKE_THRESHOLD {
        return 0.0;
    }

    let spike_strength = (m.abs() / SPIKE_THRESHOLD).min(3.0) / 3.0;
    let decay_amplifier = 1.0 + f.time_decay;
    let magnitude = (spike_strength * decay_amplifier).min(1.0);

    // Spike direction preserved: up → positive, down → negative
    let direction = m.signum();
    (direction * magnitude).clamp(-1.0, 1.0)
}
