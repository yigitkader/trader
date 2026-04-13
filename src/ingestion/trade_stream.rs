use crate::types::{RawTrade, TradeSide};
use anyhow::Context;
use futures_util::StreamExt;
use serde::Deserialize;
use std::collections::{HashMap, VecDeque};
use std::sync::Arc;
use tokio::sync::Mutex;
use tokio::time::{sleep, Duration};
use tokio_tungstenite::connect_async;

const WS_URL: &str = "wss://ws-subscriptions-clob.polymarket.com/ws/market";
const BUFFER_SIZE: usize = 50;
const WS_RECONNECT_SECS: u64 = 5;

#[derive(Deserialize)]
struct WsTrade {
    market: String,
    side: String,
    price: f32,
    size: f64,
    timestamp: u64,
}

pub type TradeBuffer = VecDeque<RawTrade>;
pub type SharedTradeBuffers = Arc<Mutex<HashMap<String, TradeBuffer>>>;

pub fn new_buffer() -> TradeBuffer {
    VecDeque::with_capacity(BUFFER_SIZE)
}

pub fn push_trade(buffer: &mut TradeBuffer, trade: RawTrade) {
    if buffer.len() == BUFFER_SIZE {
        buffer.pop_front();
    }
    buffer.push_back(trade);
}

/// Bağlantı koptuğunda veya stream bittiğinde yeniden dener.
pub async fn run_market_ws(market_id: String, buffers: SharedTradeBuffers) {
    loop {
        match connect_and_stream(&market_id, &buffers).await {
            Ok(()) => eprintln!("ws {}: stream ended, reconnecting…", market_id),
            Err(e) => eprintln!("ws {}: {:#}, reconnecting…", market_id, e),
        }
        sleep(Duration::from_secs(WS_RECONNECT_SECS)).await;
    }
}

async fn connect_and_stream(
    market_id: &str,
    buffers: &SharedTradeBuffers,
) -> anyhow::Result<()> {
    let url = format!("{}?market={}", WS_URL, market_id);
    let (ws, _) = connect_async(&url)
        .await
        .with_context(|| format!("WebSocket connect {}", url))?;
    let (_, mut read) = ws.split();

    while let Some(msg) = read.next().await {
        let msg = msg?;
        let Ok(text) = msg.into_text() else {
            continue;
        };

        let Ok(trade) = serde_json::from_str::<WsTrade>(&text) else {
            continue;
        };

        let side = if trade.side.eq_ignore_ascii_case("BUY") {
            TradeSide::Buy
        } else {
            TradeSide::Sell
        };

        let raw = RawTrade {
            market_id: trade.market.clone(),
            side,
            price: trade.price,
            size: trade.size,
            timestamp: trade.timestamp,
        };

        let mut map = buffers.lock().await;
        let buf = map.entry(trade.market).or_insert_with(new_buffer);
        push_trade(buf, raw);
    }

    Ok(())
}
