pub mod decision;
pub mod ranker;
pub mod scorer;

use crate::strategy_params::StrategyParams;
use crate::types::{Decision, Market, ScoredMarket, SignalSet};

const SECS_PER_YEAR: f32 = 365.25 * 86400.0;

pub fn process(signals: &SignalSet, market: &Market, strategy: &StrategyParams) -> ScoredMarket {
    let raw_score = scorer::compute(signals);
    let model_prob = scorer::sigmoid(raw_score);

    let edge = model_prob - market.yes_price;
    let edge_abs = edge.abs();

    let ttr = market.time_to_resolution.max(3600);
    let ref_secs = strategy.ttr_edge_ref_secs.max(3600) as f32;
    let edge_req = strategy.min_edge * (ttr as f32 / ref_secs).powf(strategy.ttr_edge_exponent);

    let years = (market.time_to_resolution as f32 / SECS_PER_YEAR).max(1.0 / SECS_PER_YEAR);
    let annualized_edge = edge / years;

    let dominant_signal = decision::dominant(signals, strategy);

    let decision = if edge_abs < edge_req {
        Decision::Skip
    } else {
        let raw = if edge > 0.0 {
            Decision::BuyYes
        } else {
            Decision::BuyNo
        };
        decision::apply_price_gate(raw, market, strategy)
    };

    ScoredMarket {
        market_id: market.id.clone(),
        confidence: model_prob,
        edge_score: edge,
        annualized_edge,
        decision,
        dominant_signal,
    }
}
