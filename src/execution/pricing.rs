//! Taker (spread aşan) vs maker (bid tarafına limit) limit fiyatı.

use crate::execution::config::OrderStyle;
use crate::ingestion::book_feed::BookSnapshot;
use crate::types::{Decision, Market};

fn mid_for_decision(market: &Market, decision: &Decision) -> f32 {
    match decision {
        Decision::BuyYes => market.yes_price,
        Decision::BuyNo => market.no_price,
        Decision::Skip => 0.5,
    }
}

/// Maker: mümkünse best_bid+tick ile kuyruğa gir; taker: mid+slippage (varsa ask altında sıkıştır).
pub fn limit_price_buy(
    market: &Market,
    decision: &Decision,
    slippage: f32,
    style: OrderStyle,
    book: Option<&BookSnapshot>,
) -> f32 {
    let mid = mid_for_decision(market, decision);
    let slip = slippage.max(0.0);

    match style {
        OrderStyle::Taker => {
            let raw = (mid + slip).clamp(0.01, 0.99);
            if let Some(b) = book {
                if let Some(ask) = b.best_ask {
                    let cap = (ask - b.tick * 0.5).max(0.01);
                    return raw.min(cap).max(0.01);
                }
            }
            raw
        }
        OrderStyle::Maker => {
            if let Some(b) = book {
                if let (Some(bb), Some(ba)) = (b.best_bid, b.best_ask) {
                    if ba > bb && b.tick.is_finite() && b.tick > 0.0 {
                        let join = (bb + b.tick).min(ba - b.tick);
                        if join.is_finite() && join > bb {
                            return join.clamp(0.01, 0.99);
                        }
                    }
                }
            }
            let spread = market.spread.max(0.0);
            (mid - spread * 0.25).clamp(0.01, 0.99)
        }
    }
}
