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

    // İşlem yokken sell_vol==0 olur; 2.0 dönmek tüm piyasalara sahte "alıcı baskısı" verirdi.
    if buy_vol == 0.0 && sell_vol == 0.0 {
        return 1.0;
    }
    if sell_vol == 0.0 {
        return 2.0;
    }
    if buy_vol == 0.0 {
        return 0.0;
    }

    let r = buy_vol / sell_vol;
    (r.clamp(0.0, 20.0)) as f32
}