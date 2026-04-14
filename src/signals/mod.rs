pub mod absorption;
pub mod book_skew;
pub mod fake_move;
pub mod panic;

use crate::strategy_params::StrategyParams;
use crate::types::{Features, SignalSet};

pub fn compute_all(features: &Features, strategy: &StrategyParams) -> SignalSet {
    SignalSet {
        market_id: features.market_id.clone(),
        fake_move: fake_move::compute(features, strategy),
        absorption: absorption::compute(features, strategy),
        panic: panic::compute(features, strategy),
        book_skew: book_skew::compute(features),
    }
}