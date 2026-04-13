pub mod absorption;
pub mod fake_move;
pub mod panic;

use crate::types::{Features, SignalSet};

pub fn compute_all(features: &Features) -> SignalSet {
    SignalSet {
        market_id: features.market_id.clone(),
        fake_move: fake_move::compute(features),
        absorption: absorption::compute(features),
        panic: panic::compute(features),
    }
}