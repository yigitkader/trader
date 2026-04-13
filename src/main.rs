mod engine;
mod features;
mod ingestion;
mod logger;
mod signals;
mod types;

use ingestion::trade_stream::SharedTradeBuffers;
use reqwest::Client;
use std::collections::{HashMap, HashSet, VecDeque};
use std::sync::Arc;
use tokio::sync::Mutex;
use tokio::task::JoinHandle;
use tokio::time::{sleep, Duration};
use types::{now_secs, RawTrade};

const TICK_SECS: u64 = 2;

#[tokio::main]
async fn main() {
    let client = Client::new();
    let mut price_windows: HashMap<String, VecDeque<(u64, f32)>> = HashMap::new();
    let trade_buffers: SharedTradeBuffers = Arc::new(Mutex::new(HashMap::new()));
    let mut ranker = engine::ranker::Ranker::new();
    let mut ws_handles: HashMap<String, JoinHandle<()>> = HashMap::new();

    println!("Polymarket bot başladı — analiz modu (execution yok)");

    loop {
        ranker.clear();

        // 1. Marketleri çek
        let markets = match ingestion::price_feed::fetch_markets(&client).await {
            Ok(m) => m,
            Err(e) => {
                eprintln!("fetch error: {}", e);
                sleep(Duration::from_secs(TICK_SECS)).await;
                continue;
            }
        };

        let tradeable_ids: HashSet<String> = markets
            .iter()
            .filter(|m| ingestion::market_meta::is_tradeable(m))
            .map(|m| m.id.clone())
            .collect();

        // İşlem görmeyen piyasaların WS görevlerini durdur, buffer'ı temizle
        ws_handles.retain(|id, h| {
            if tradeable_ids.contains(id) {
                true
            } else {
                h.abort();
                false
            }
        });
        {
            let mut g = trade_buffers.lock().await;
            g.retain(|id, _| tradeable_ids.contains(id));
        }

        // Yeni işlem gören piyasalar için WebSocket görevi
        for id in &tradeable_ids {
            if ws_handles.contains_key(id) {
                continue;
            }
            let bufs = Arc::clone(&trade_buffers);
            let mid = id.clone();
            let h = tokio::spawn(ingestion::trade_stream::run_market_ws(mid, bufs));
            ws_handles.insert(id.clone(), h);
        }

        let now = now_secs();

        for market in &markets {
            // 2. Filtrele
            if !ingestion::market_meta::is_tradeable(market) {
                continue;
            }

            // 3. Price window güncelle
            let window = price_windows
                .entry(market.id.clone())
                .or_insert_with(VecDeque::new);
            features::momentum::push_price(window, now, market.yes_price);

            // 4. Trade buffer (WebSocket görevleri doldurur)
            let trades: VecDeque<RawTrade> = trade_buffers
                .lock()
                .await
                .get(&market.id)
                .cloned()
                .unwrap_or_default();

            // 5. Features hesapla
            let feats = features::compute_all(market, window, &trades);

            // 6. Sinyaller
            let sigs = signals::compute_all(&feats);

            // 7. Engine
            let scored = engine::process(&sigs, &market.id, market.yes_price);

            // 8. Sadece anlamlı sinyalleri logla
            if !matches!(scored.decision, types::Decision::Skip) {
                let entry = types::LogEntry {
                    timestamp: now,
                    market_id: market.id.clone(),
                    market_question: market.question.clone(),
                    price_at_signal: market.yes_price,
                    confidence: scored.confidence,
                    edge_score: scored.edge_score,
                    decision: scored.decision.clone(),
                    dominant_signal: scored.dominant_signal.clone(),
                    features_snapshot: feats,
                };

                if let Err(e) = logger::writer::write(&entry) {
                    eprintln!("log error: {}", e);
                }

                println!(
                    "[SINYAL] {} | price: {:.2} | conf: {:.2} | {:?} | {:?}",
                    market.question,
                    market.yes_price,
                    scored.confidence,
                    scored.decision,
                    scored.dominant_signal
                );
            }

            ranker.push(scored);
        }

        // Top fırsatları göster
        println!("\n--- TOP FIRSATLAR ---");
        for m in ranker.top_n() {
            println!("  {} | conf: {:.3} | {:?}", m.market_id, m.confidence, m.decision);
        }

        sleep(Duration::from_secs(TICK_SECS)).await;
    }
}
