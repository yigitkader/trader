use crate::types::Features;

// Large price move on thin volume → likely reversal.
// SIGNED: positive momentum (YES up) on thin tape → expect reversion down → negative signal.
// Negative momentum (YES down) on thin tape → expect reversion up → positive signal.

const MOMENTUM_THRESHOLD: f32 = 0.03;
const VOLUME_THRESHOLD: f32 = 0.001;

pub fn compute(f: &Features) -> f32 {
    let m = f.momentum;
    if m.abs() <= MOMENTUM_THRESHOLD {
        return 0.0;
    }
    if f.reaction_speed <= VOLUME_THRESHOLD {
        return 0.0;
    }

    let momentum_strength = (m.abs() / MOMENTUM_THRESHOLD).min(3.0) / 3.0;
    let volume_weakness = (f.reaction_speed / VOLUME_THRESHOLD).min(3.0) / 3.0;
    let magnitude = (momentum_strength + volume_weakness) / 2.0;

    // Fake move UP → expect reversion DOWN → bearish YES → negative
    let direction = -m.signum();
    (direction * magnitude).clamp(-1.0, 1.0)
}
