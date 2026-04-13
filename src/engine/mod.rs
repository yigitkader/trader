pub mod decision;
pub mod ranker;
pub mod scorer;

use crate::types::{Decision, Market, ScoredMarket, SignalSet};

/// Minimum |edge| to overcome spread + fees (~2% round-trip on Polymarket).
const MIN_EDGE: f32 = 0.025;

pub fn process(signals: &SignalSet, market: &Market) -> ScoredMarket {
    let raw_score = scorer::compute(signals);
    let model_prob = scorer::sigmoid(raw_score);

    let edge = model_prob - market.yes_price;
    let dominant_signal = decision::dominant(signals);

    let decision = if edge.abs() < MIN_EDGE {
        Decision::Skip
    } else {
        let raw = if edge > 0.0 {
            Decision::BuyYes
        } else {
            Decision::BuyNo
        };
        decision::apply_price_gate(raw, market)
    };

    ScoredMarket {
        market_id: market.id.clone(),
        confidence: model_prob,
        edge_score: edge,
        decision,
        dominant_signal,
    }
}
