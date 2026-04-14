use crate::features::effective_tape_momentum;
use crate::strategy_params::StrategyParams;
use crate::types::Features;

// Large price move on thin volume → likely reversal.
// SIGNED: positive momentum (YES up) on thin tape → expect reversion down → negative signal.
// Negative momentum (YES down) on thin tape → expect reversion up → positive signal.

pub fn compute(f: &Features, strategy: &StrategyParams) -> f32 {
    let m = effective_tape_momentum(f);
    if m.abs() <= strategy.fake_momentum_threshold {
        return 0.0;
    }
    // WS trade yoksa `reaction_speed` 0 olur; bu durumda "thin tape" kabul edip momentuma göre fake_move üret.
    // Trade varsa reaction_speed ile filtrele.
    let has_tape = f.trade_count > 0;
    if has_tape && f.reaction_speed <= strategy.fake_reaction_min {
        return 0.0;
    }

    let momentum_strength = (m.abs() / strategy.fake_momentum_threshold).min(3.0) / 3.0;
    let volume_weakness = if has_tape {
        (f.reaction_speed / strategy.fake_reaction_min).min(3.0) / 3.0
    } else {
        1.0
    };
    let magnitude = (momentum_strength + volume_weakness) / 2.0;

    // Fake move UP → expect reversion DOWN → bearish YES → negative
    let direction = -m.signum();
    (direction * magnitude).clamp(-1.0, 1.0)
}
