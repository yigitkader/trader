//! CLOB `POST /books` ile L2 özet: bid/ask dengesizliği ve maker fiyatı için best bid/ask.

use std::collections::HashMap;
use std::str::FromStr as _;

use polymarket_client_sdk::clob::types::request::OrderBookSummaryRequest;
use polymarket_client_sdk::clob::types::response::OrderBookSummaryResponse;
use polymarket_client_sdk::clob::{Client, Config};
use polymarket_client_sdk::types::U256;
use rust_decimal::Decimal;
use rust_decimal::prelude::ToPrimitive;

/// YES/NO outcome token için defter özeti (token_id string anahtarlı).
#[derive(Debug, Clone)]
pub struct BookSnapshot {
    /// Toplam bid hacminin (bid+ask) içindeki payı, [0,1]. 0.5 = dengeli.
    pub imbalance: f32,
    pub best_bid: Option<f32>,
    pub best_ask: Option<f32>,
    pub tick: f32,
}

const BOOK_CHUNK: usize = 20;

fn snapshot_from_book(book: &OrderBookSummaryResponse, depth: usize) -> BookSnapshot {
    let bids_vol: f64 = book
        .bids
        .iter()
        .take(depth)
        .filter_map(|l| l.size.to_f64())
        .sum();
    let asks_vol: f64 = book
        .asks
        .iter()
        .take(depth)
        .filter_map(|l| l.size.to_f64())
        .sum();
    let tot = bids_vol + asks_vol;
    let imbalance = if tot < 1e-12 {
        0.5
    } else {
        (bids_vol / tot) as f32
    };

    let best_bid = book.bids.first().and_then(|l| l.price.to_f32());
    let best_ask = book.asks.first().and_then(|l| l.price.to_f32());
    let tick_d: Decimal = book.tick_size.into();
    let tick = tick_d.to_f32().filter(|t| t.is_finite()).unwrap_or(0.01);

    BookSnapshot {
        imbalance,
        best_bid,
        best_ask,
        tick,
    }
}

fn token_key(asset: &U256) -> String {
    asset.to_string()
}

/// `token_ids` için batch kitap çeker; hata olursa kısmi harita döner.
pub async fn fetch_snapshots(
    clob_base: &str,
    token_ids: &[String],
    depth_levels: usize,
) -> HashMap<String, BookSnapshot> {
    let mut out = HashMap::new();
    if token_ids.is_empty() {
        return out;
    }

    let host = clob_base.trim_end_matches('/');
    let Ok(client) = Client::new(host, Config::default()) else {
        return out;
    };

    for chunk in token_ids.chunks(BOOK_CHUNK) {
        let mut reqs = Vec::with_capacity(chunk.len());
        for id in chunk {
            let Ok(tid) = U256::from_str(id) else {
                continue;
            };
            reqs.push(
                OrderBookSummaryRequest::builder()
                    .token_id(tid)
                    .build(),
            );
        }
        if reqs.is_empty() {
            continue;
        }

        match client.order_books(&reqs).await {
            Ok(books) => {
                for book in books {
                    let snap = snapshot_from_book(&book, depth_levels);
                    out.insert(token_key(&book.asset_id), snap);
                }
            }
            Err(e) => {
                eprintln!("ingestion/book_feed: order_books batch hata: {e}");
            }
        }
    }

    out
}
