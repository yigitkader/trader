use crate::features::effective_tape_momentum;
use crate::strategy_params::StrategyParams;
use crate::types::Features;

// Large spike: rapid momentum move. Near expiry amplified.
// SIGNED: spike UP → trend continuation → bullish YES → positive.
// Spike DOWN → trend continuation → bearish YES → negative.

pub fn compute(f: &Features, strategy: &StrategyParams) -> f32 {
    let m = effective_tape_momentum(f);
    if m.abs() <= strategy.panic_spike_threshold {
        return 0.0;
    }

    let spike_strength = (m.abs() / strategy.panic_spike_threshold).min(3.0) / 3.0;
    let decay_amplifier = 1.0 + f.time_decay;
    let magnitude = (spike_strength * decay_amplifier).min(1.0);

    // Spike direction preserved: up → positive, down → negative
    let direction = m.signum();
    (direction * magnitude).clamp(-1.0, 1.0)
}
