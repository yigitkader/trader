//! Polymarket CLOB `ws/market`: bağlantı sonrası JSON subscribe, `event_type` ile dispatch,
//! periyodik `PING` (metin). Kaynak: `polymarket.txt` araştırma notları.

use crate::types::{RawTrade, TradeSide};
use anyhow::Context;
use futures_util::{SinkExt, StreamExt};
use serde::Serialize;
use serde_json::Value;
use std::collections::{HashMap, HashSet, VecDeque};
use std::sync::Arc;
use std::time::Duration;
use tokio::sync::{mpsc, Mutex};
use tokio::time::{interval, MissedTickBehavior};
use tokio_tungstenite::connect_async;
use tokio_tungstenite::tungstenite::Message;

const WS_URL: &str = "wss://ws-subscriptions-clob.polymarket.com/ws/market";
const BUFFER_SIZE: usize = 50;
const MAX_ASSETS_PER_MESSAGE: usize = 200;
const PING_INTERVAL_SECS: u64 = 10;
const WS_RECONNECT_SECS: u64 = 5;

pub type TradeBuffer = VecDeque<RawTrade>;
pub type SharedTradeBuffers = Arc<Mutex<HashMap<String, TradeBuffer>>>;

#[derive(Serialize)]
struct InitialSubscribe<'a> {
    assets_ids: &'a [String],
    #[serde(rename = "type")]
    msg_type: &'static str,
    custom_feature_enabled: bool,
}

#[derive(Serialize)]
struct DynamicSubscribe<'a> {
    assets_ids: &'a [String],
    operation: &'static str,
}

pub fn new_buffer() -> TradeBuffer {
    VecDeque::with_capacity(BUFFER_SIZE)
}

pub fn push_trade(buffer: &mut TradeBuffer, trade: RawTrade) {
    if buffer.len() == BUFFER_SIZE {
        buffer.pop_front();
    }
    buffer.push_back(trade);
}

/// Ana döngüden her tick’te güncel `asset_id` listesini gönder; hub tek (veya parçalı) bağlantıyı yönetir.
pub fn spawn_trade_hub(buffers: SharedTradeBuffers) -> mpsc::Sender<Vec<String>> {
    let (tx, rx) = mpsc::channel::<Vec<String>>(64);
    tokio::spawn(run_trade_hub(buffers, rx));
    tx
}

async fn run_trade_hub(buffers: SharedTradeBuffers, mut cmd_rx: mpsc::Receiver<Vec<String>>) {
    let mut desired: Vec<String> = Vec::new();

    loop {
        while desired.is_empty() {
            match cmd_rx.recv().await {
                Some(ids) if !ids.is_empty() => desired = ids,
                Some(_) => {}
                None => return,
            }
        }

        match maintain_connection(&buffers, &mut cmd_rx, &mut desired).await {
            Ok(()) => {}
            Err(e) => eprintln!("trade hub: {:#}", e),
        }

        tokio::time::sleep(Duration::from_secs(WS_RECONNECT_SECS)).await;
    }
}

/// `desired` condition: boş liste gelirse bağlantıyı kapatıp çıkar (üst döngü yeni liste bekler).
async fn maintain_connection(
    buffers: &SharedTradeBuffers,
    cmd_rx: &mut mpsc::Receiver<Vec<String>>,
    desired: &mut Vec<String>,
) -> anyhow::Result<()> {
    let (ws, _) = connect_async(WS_URL)
        .await
        .with_context(|| format!("WebSocket connect {}", WS_URL))?;
    let (mut write, mut read) = ws.split();

    let mut subscribed: HashSet<String> = HashSet::new();
    subscribe_full(&mut write, &mut subscribed, desired).await?;

    let mut ping = interval(Duration::from_secs(PING_INTERVAL_SECS));
    ping.set_missed_tick_behavior(MissedTickBehavior::Delay);

    loop {
        tokio::select! {
            _ = ping.tick() => {
                write
                    .send(Message::Text("PING".into()))
                    .await
                    .context("WS PING send")?;
            }
            cmd = cmd_rx.recv() => {
                match cmd {
                    Some(ids) if ids.is_empty() => {
                        return Ok(());
                    }
                    Some(ids) => {
                        *desired = ids;
                        let to_add: Vec<String> = desired
                            .iter()
                            .filter(|id| !subscribed.contains(*id))
                            .cloned()
                            .collect();
                        subscribe_incremental(&mut write, &mut subscribed, &to_add).await?;
                    }
                    None => return Ok(()),
                }
            }
            msg = read.next() => {
                match msg {
                    Some(Ok(Message::Text(t))) => {
                        if t.as_str() == "PONG" {
                            continue;
                        }
                        apply_parsed(buffers, &t).await;
                    }
                    Some(Ok(Message::Ping(_))) => {}
                    Some(Ok(Message::Pong(_))) => {}
                    Some(Ok(Message::Close(_))) => return Ok(()),
                    Some(Ok(Message::Binary(_))) | Some(Ok(Message::Frame(_))) => {}
                    Some(Err(e)) => return Err(e.into()),
                    None => return Ok(()),
                }
            }
        }
    }
}

async fn subscribe_full<W>(
    write: &mut W,
    subscribed: &mut HashSet<String>,
    desired: &[String],
) -> anyhow::Result<()>
where
    W: SinkExt<Message> + Unpin,
    W::Error: std::error::Error + Send + Sync + 'static,
{
    if desired.is_empty() {
        return Ok(());
    }

    let mut chunks = desired.chunks(MAX_ASSETS_PER_MESSAGE);
    let Some(first) = chunks.next() else {
        return Ok(());
    };

    let init = InitialSubscribe {
        assets_ids: first,
        msg_type: "market",
        custom_feature_enabled: true,
    };
    write
        .send(Message::Text(serde_json::to_string(&init)?.into()))
        .await
        .map_err(|e| anyhow::anyhow!(e))?;
    for id in first {
        subscribed.insert(id.clone());
    }

    for chunk in chunks {
        subscribe_incremental(write, subscribed, chunk).await?;
    }

    Ok(())
}

async fn subscribe_incremental<W>(
    write: &mut W,
    subscribed: &mut HashSet<String>,
    ids: &[String],
) -> anyhow::Result<()>
where
    W: SinkExt<Message> + Unpin,
    W::Error: std::error::Error + Send + Sync + 'static,
{
    for chunk in ids.chunks(MAX_ASSETS_PER_MESSAGE) {
        if chunk.is_empty() {
            continue;
        }
        let msg = DynamicSubscribe {
            assets_ids: chunk,
            operation: "subscribe",
        };
        write
            .send(Message::Text(serde_json::to_string(&msg)?.into()))
            .await
            .map_err(|e| anyhow::anyhow!(e))?;
        for id in chunk {
            subscribed.insert(id.clone());
        }
    }
    Ok(())
}

async fn apply_parsed(buffers: &SharedTradeBuffers, text: &str) {
    let trades = parse_ws_text(text);
    if trades.is_empty() {
        return;
    }
    let mut map = buffers.lock().await;
    for trade in trades {
        let key = trade.market_id.clone();
        let buf = map.entry(key).or_insert_with(new_buffer);
        push_trade(buf, trade);
    }
}

fn parse_ws_text(text: &str) -> Vec<RawTrade> {
    let v: Value = match serde_json::from_str(text) {
        Ok(v) => v,
        Err(_) => return Vec::new(),
    };
    let Some(et) = v.get("event_type").and_then(|x| x.as_str()) else {
        return Vec::new();
    };
    match et {
        "last_trade_price" => parse_last_trade_price(&v).into_iter().collect(),
        "price_change" => parse_price_change(&v),
        _ => Vec::new(),
    }
}

fn parse_last_trade_price(v: &Value) -> Option<RawTrade> {
    let market = v.get("market")?.as_str()?.to_string();
    let side_s = v.get("side")?.as_str()?;
    let price = json_f32(v, "price")?;
    let size = json_f64(v, "size")?;
    let timestamp = json_u64(v, "timestamp").unwrap_or(0);
    let side = if side_s.eq_ignore_ascii_case("BUY") {
        TradeSide::Buy
    } else {
        TradeSide::Sell
    };
    Some(RawTrade {
        market_id: market,
        side,
        price,
        size,
        timestamp,
    })
}

fn parse_price_change(v: &Value) -> Vec<RawTrade> {
    let Some(market) = v.get("market").and_then(|m| m.as_str()) else {
        return Vec::new();
    };
    let market = market.to_string();
    let ts = json_u64(v, "timestamp").unwrap_or(0);
    let Some(arr) = v.get("price_changes").and_then(|a| a.as_array()) else {
        return Vec::new();
    };

    let mut out = Vec::new();
    for item in arr {
        let side_s = item.get("side").and_then(|s| s.as_str()).unwrap_or("");
        let Some(price) = json_f32(item, "price") else {
            continue;
        };
        let Some(size) = json_f64(item, "size") else {
            continue;
        };
        let side = if side_s.eq_ignore_ascii_case("BUY") {
            TradeSide::Buy
        } else {
            TradeSide::Sell
        };
        out.push(RawTrade {
            market_id: market.clone(),
            side,
            price,
            size,
            timestamp: ts,
        });
    }
    out
}

fn json_f32(v: &Value, key: &str) -> Option<f32> {
    let x = v.get(key)?;
    if let Some(s) = x.as_str() {
        return s.parse().ok();
    }
    x.as_f64().map(|n| n as f32)
}

fn json_f64(v: &Value, key: &str) -> Option<f64> {
    let x = v.get(key)?;
    if let Some(s) = x.as_str() {
        return s.parse().ok();
    }
    x.as_f64()
}

fn json_u64(v: &Value, key: &str) -> Option<u64> {
    let x = v.get(key)?;
    if let Some(s) = x.as_str() {
        return s.parse().ok();
    }
    x.as_u64()
        .or_else(|| x.as_i64().map(|i| u64::try_from(i).ok()).flatten())
}
