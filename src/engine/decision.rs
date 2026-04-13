use crate::types::{Decision, DominantSignal, Market, SignalSet};

const MIN_OUTCOME_MID: f32 = 0.03;
const MAX_OUTCOME_MID: f32 = 0.97;

/// Uç fiyatlarda kararı Skip yap (illikit / çözülmüş gösterge).
pub fn apply_price_gate(decision: Decision, market: &Market) -> Decision {
    match decision {
        Decision::Skip => Decision::Skip,
        Decision::BuyYes => {
            let y = market.yes_price;
            if y < MIN_OUTCOME_MID || y > MAX_OUTCOME_MID {
                Decision::Skip
            } else {
                Decision::BuyYes
            }
        }
        Decision::BuyNo => {
            let n = market.no_price;
            if n < MIN_OUTCOME_MID || n > MAX_OUTCOME_MID {
                Decision::Skip
            } else {
                Decision::BuyNo
            }
        }
    }
}

pub fn dominant(s: &SignalSet) -> DominantSignal {
    let af = s.fake_move.abs();
    let aa = s.absorption.abs();
    let ap = s.panic.abs();
    let max = af.max(aa).max(ap);

    if max < 0.05 {
        return DominantSignal::Mixed;
    }

    if (af - max).abs() < 0.02 && af >= aa && af >= ap {
        DominantSignal::FakeMove
    } else if (aa - max).abs() < 0.02 && aa >= ap {
        DominantSignal::Absorption
    } else if ap > 0.0 {
        DominantSignal::Panic
    } else {
        DominantSignal::Mixed
    }
}