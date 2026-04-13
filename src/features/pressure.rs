use crate::types::{RawTrade, TradeSide};
use std::collections::VecDeque;

// buy_volume / sell_volume
// > 1.0  →  alıcı baskısı
// < 1.0  →  satıcı baskısı
// = 1.0  →  denge

pub fn compute(trades: &VecDeque<RawTrade>) -> f32 {
    let mut buy_vol = 0.0_f64;
    let mut sell_vol = 0.0_f64;

    for trade in trades {
        match trade.side {
            TradeSide::Buy => buy_vol += trade.size,
            TradeSide::Sell => sell_vol += trade.size,
        }
    }

    if sell_vol == 0.0 {
        return 2.0; // tam alıcı baskısı
    }

    (buy_vol / sell_vol) as f32
}