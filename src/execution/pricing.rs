//! Taker (spread aşan) vs maker (bid tarafına limit) limit fiyatı.

use crate::execution::config::{OrderStyle, ExecutionConfig};
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
/// `maker_strict`: kitap yoksa veya güvenli maker kotasyonu yoksa hata — emir gönderilmez.
pub fn limit_price_buy(
    market: &Market,
    decision: &Decision,
    cfg: &ExecutionConfig,
    book: Option<&BookSnapshot>,
) -> Result<f32, &'static str> {
    let mid = mid_for_decision(market, decision);
    let slip = cfg.price_slippage.max(0.0);

    match cfg.order_style {
        OrderStyle::Taker => {
            let raw = (mid + slip).clamp(0.01, 0.99);
            let out = if let Some(b) = book {
                if let Some(ask) = b.best_ask {
                    let cap = (ask - b.tick * 0.5).max(0.01);
                    raw.min(cap).max(0.01)
                } else {
                    raw
                }
            } else {
                raw
            };
            Ok(out)
        }
        OrderStyle::Maker => {
            if let Some(b) = book {
                if let (Some(bb), Some(ba)) = (b.best_bid, b.best_ask) {
                    if ba > bb && b.tick.is_finite() && b.tick > 0.0 {
                        let join = (bb + b.tick).min(ba - b.tick);
                        if join.is_finite() && join > bb {
                            return Ok(join.clamp(0.01, 0.99));
                        }
                    }
                }
                if cfg.maker_strict {
                    return Err("maker_strict: bid/ask veya spread yetersiz");
                }
            } else if cfg.maker_strict {
                return Err("maker_strict: L2 kitabı yok");
            }
            let spread = market.spread.max(0.0);
            Ok((mid - spread * 0.25).clamp(0.01, 0.99))
        }
    }
}
