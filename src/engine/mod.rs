pub mod decision;
pub mod ranker;
pub mod scorer;

use crate::types::{ScoredMarket, SignalSet};

pub fn process(signals: &SignalSet, market_id: &str, _current_price: f32) -> ScoredMarket {
    let edge_score = scorer::compute(signals);
    let confidence = scorer::sigmoid(edge_score);
    let decision = decision::decide(confidence);
    let dominant_signal = decision::dominant(signals);

    ScoredMarket {
        market_id: market_id.to_string(),
        confidence,
        edge_score,
        decision,
        dominant_signal,
    }
}