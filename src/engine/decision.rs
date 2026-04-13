use crate::types::{Decision, DominantSignal, SignalSet};

const BUY_THRESHOLD: f32 = 0.65;
const SHORT_THRESHOLD: f32 = 0.35;

pub fn decide(confidence: f32) -> Decision {
    if confidence > BUY_THRESHOLD {
        Decision::BuyYes
    } else if confidence < SHORT_THRESHOLD {
        Decision::BuyNo
    } else {
        Decision::Skip
    }
}

pub fn dominant(s: &SignalSet) -> DominantSignal {
    let max = s.fake_move.max(s.absorption).max(s.panic);

    if max < 0.1 {
        return DominantSignal::Mixed;
    }

    if (s.fake_move - max).abs() < 0.05 {
        DominantSignal::FakeMove
    } else if (s.absorption - max).abs() < 0.05 {
        DominantSignal::Absorption
    } else {
        DominantSignal::Panic
    }
}