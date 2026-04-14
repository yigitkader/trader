use crate::strategy_params::StrategyParams;
use crate::types::{Decision, DominantSignal, Market, SignalSet};

/// Uç fiyatlarda kararı Skip yap (illikit / çözülmüş gösterge).
pub fn apply_price_gate(decision: Decision, market: &Market, strategy: &StrategyParams) -> Decision {
    match decision {
        Decision::Skip => Decision::Skip,
        Decision::BuyYes => {
            let y = market.yes_price;
            if y < strategy.min_outcome_mid || y > strategy.max_outcome_mid {
                Decision::Skip
            } else {
                Decision::BuyYes
            }
        }
        Decision::BuyNo => {
            let n = market.no_price;
            if n < strategy.min_outcome_mid || n > strategy.max_outcome_mid {
                Decision::Skip
            } else {
                Decision::BuyNo
            }
        }
    }
}

pub fn dominant(s: &SignalSet, strategy: &StrategyParams) -> DominantSignal {
    let af = s.fake_move.abs();
    let aa = s.absorption.abs();
    let ap = s.panic.abs();
    let max = af.max(aa).max(ap);
    let eps = strategy.dominant_tie_eps;

    if max < strategy.dominant_mixed_max {
        return DominantSignal::Mixed;
    }

    if (af - max).abs() < eps && af >= aa && af >= ap {
        DominantSignal::FakeMove
    } else if (aa - max).abs() < eps && aa >= ap {
        DominantSignal::Absorption
    } else if ap > 0.0 {
        DominantSignal::Panic
    } else {
        DominantSignal::Mixed
    }
}