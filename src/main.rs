mod engine;
mod execution;
mod features;
mod http_client;
mod ingestion;
mod logger;
mod signals;
mod types;

use ingestion::trade_stream::SharedTradeBuffers;
use std::collections::{HashMap, HashSet, VecDeque};
use std::sync::Arc;
use tokio::sync::Mutex;
use tokio::time::{sleep, Duration};
use types::{now_secs, RawTrade};

const TICK_SECS: u64 = 2;

/// Kalan süre (Gamma `endDateIso` → `Market.time_to_resolution`).
fn fmt_ttr_remaining(secs: u64) -> String {
    if secs == 0 {
        return "bilinmiyor/0".into();
    }
    let d = secs / 86_400;
    let h = (secs % 86_400) / 3600;
    let m = (secs % 3600) / 60;
    if d > 0 {
        format!("{d}gün {h}sa")
    } else if h > 0 {
        format!("{h}sa {m}dk")
    } else {
        format!("{m}dk")
    }
}

fn truncate(s: &str, max: usize) -> String {
    let t = s.trim();
    if t.chars().count() <= max {
        return t.to_string();
    }
    let mut out = String::new();
    for (i, ch) in t.chars().enumerate() {
        if i >= max.saturating_sub(3) {
            break;
        }
        out.push(ch);
    }
    out.push_str("...");
    out
}

#[tokio::main]
async fn main() {
    let exec_cfg = execution::ExecutionConfig::load();
    let client = http_client::build().expect("HTTP client (reqwest) kurulamadı");
    let mut price_windows: HashMap<String, VecDeque<(u64, f32)>> = HashMap::new();
    let trade_buffers: SharedTradeBuffers = Arc::new(Mutex::new(HashMap::new()));
    let mut ranker = engine::ranker::Ranker::new();

    let hub_tx = ingestion::trade_stream::spawn_trade_hub(Arc::clone(&trade_buffers));
    // Bir önceki tick'te en az bir YES asset_id ile hub'a liste gönderildi mi? (Boş send spam'ini önler.)
    let mut had_ws_asset_subscriptions = false;
    let mut tick: u64 = 0;

    println!(
        "Polymarket bot başladı — analiz + execution katmanı ({})",
        exec_cfg.describe()
    );
    let max_ttr = ingestion::market_meta::max_time_to_resolution_secs();
    println!(
        "Filtreler: TTR ≥ {}s | spread ≤ {:.2} | hacim ≥ {:.0} (Gamma) | karar eşiği conf>0.65 / <0.35{}",
        ingestion::market_meta::MIN_TTR_SECS,
        ingestion::market_meta::MAX_SPREAD,
        ingestion::market_meta::MIN_VOLUME,
        max_ttr.map_or(String::new(), |mx| format!(
            " | max TTR ≤ {}s (~{})",
            mx,
            fmt_ttr_remaining(mx)
        ))
    );

    loop {
        tick += 1;
        ranker.clear();

        // 1. Marketleri çek
        let markets = match ingestion::price_feed::fetch_markets(&client).await {
            Ok(m) => m,
            Err(e) => {
                eprintln!("[tick {}] fetch error: {}", tick, e);
                sleep(Duration::from_secs(TICK_SECS)).await;
                continue;
            }
        };

        let summary = ingestion::market_meta::filter_summary(&markets);

        let tradeable_market_ids: HashSet<String> = markets
            .iter()
            .filter(|m| ingestion::market_meta::is_tradeable(m))
            .map(|m| m.id.clone())
            .collect();

        // WS: YES outcome `asset_id` listesi (condition_id değil)
        let desired_assets: Vec<String> = markets
            .iter()
            .filter(|m| ingestion::market_meta::is_tradeable(m))
            .filter_map(|m| m.yes_token_id.clone())
            .collect();

        let ws_msg_sent = if !desired_assets.is_empty() {
            if hub_tx.send(desired_assets.clone()).await.is_err() {
                eprintln!("[tick {}] trade hub kanalı kapandı", tick);
                return;
            }
            had_ws_asset_subscriptions = true;
            format!("{} asset_id abonelik listesi hub'a gönderildi", desired_assets.len())
        } else if had_ws_asset_subscriptions {
            if hub_tx.send(vec![]).await.is_err() {
                eprintln!("[tick {}] trade hub kanalı kapandı", tick);
                return;
            }
            had_ws_asset_subscriptions = false;
            "abonelik temizlendi (token yok)".to_string()
        } else {
            "WS güncellemesi yok (işlem gören piyasa veya YES token yok)".to_string()
        };

        {
            let mut g = trade_buffers.lock().await;
            g.retain(|id, _| tradeable_market_ids.contains(id));
        }

        let trade_buffer_stats = {
            let g = trade_buffers.lock().await;
            let mut n_bufs = 0_usize;
            let mut n_trades = 0_usize;
            for id in &tradeable_market_ids {
                if let Some(buf) = g.get(id) {
                    n_bufs += 1;
                    n_trades += buf.len();
                }
            }
            (n_bufs, n_trades)
        };

        let id_to_question: HashMap<String, String> = markets
            .iter()
            .map(|m| (m.id.clone(), m.question.clone()))
            .collect();
        let id_to_ttr: HashMap<String, u64> = markets
            .iter()
            .map(|m| (m.id.clone(), m.time_to_resolution))
            .collect();

        let now = now_secs();

        let mut analyzed = 0_usize;
        let mut non_skip = 0_usize;
        let mut max_conf = 0.0_f32;
        let mut max_edge = 0.0_f32;

        for market in &markets {
            // 2. Filtrele
            if !ingestion::market_meta::is_tradeable(market) {
                continue;
            }
            analyzed += 1;

            // 3. Price window güncelle
            let window = price_windows
                .entry(market.id.clone())
                .or_insert_with(VecDeque::new);
            features::momentum::push_price(window, now, market.yes_price);

            // 4. Trade buffer (merkezi WS hub doldurur; key = condition_id / `market` alanı)
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

            max_conf = max_conf.max(scored.confidence);
            max_edge = max_edge.max(scored.edge_score);

            // 8. Sadece anlamlı sinyalleri logla
            if !matches!(scored.decision, types::Decision::Skip) {
                non_skip += 1;
                let entry = types::LogEntry {
                    timestamp: now,
                    market_id: market.id.clone(),
                    market_question: market.question.clone(),
                    price_at_signal: market.yes_price,
                    confidence: scored.confidence,
                    edge_score: scored.edge_score,
                    decision: scored.decision.clone(),
                    dominant_signal: scored.dominant_signal.clone(),
                    features_snapshot: feats.clone(),
                };

                if let Err(e) = logger::writer::write(&entry) {
                    eprintln!("[tick {}] log error: {}", tick, e);
                }

                println!(
                    "[tick {}] [SINYAL] {} | yes {:.2} | conf {:.2} | edge {:.3} | {:?} | {:?} | mom {:.2} press {:.2}",
                    tick,
                    truncate(&market.question, 70),
                    market.yes_price,
                    scored.confidence,
                    scored.edge_score,
                    scored.decision,
                    scored.dominant_signal,
                    feats.momentum,
                    feats.pressure
                );

                if let Err(e) =
                    execution::handle_signal(&exec_cfg, &client, market, &scored, tick).await
                {
                    eprintln!("[tick {}] execution error: {:#}", tick, e);
                }
            }

            ranker.push(scored);
        }

        // --- Özet satırı ---
        println!(
            "[tick {}] CLOB piyasa={} | işlem_gören={} (YES token: {}) | analiz_edilen={} | WS: {} | ws_buf: {} piyasa / {} trade | bu tick Skip dışı={} | max conf={:.3} edge={:.3}",
            tick,
            summary.total,
            summary.tradeable,
            summary.tradeable_with_yes_token,
            analyzed,
            ws_msg_sent,
            trade_buffer_stats.0,
            trade_buffer_stats.1,
            non_skip,
            max_conf,
            max_edge
        );
        if summary.tradeable == 0 {
            let far = if ingestion::market_meta::max_time_to_resolution_secs().is_some() {
                format!(" | çözüm çok uzak → {}", summary.fail_ttr_too_far)
            } else {
                String::new()
            };
            println!(
                "         └─ Çoğu kayıt genelde kapalı/eski: TTR<{}s → {} piyasa | spread>{:.2} → {} | hacim<{:.0} → {}{}",
                ingestion::market_meta::MIN_TTR_SECS,
                summary.fail_time_to_resolution,
                ingestion::market_meta::MAX_SPREAD,
                summary.fail_spread,
                ingestion::market_meta::MIN_VOLUME,
                summary.fail_volume,
                far,
            );
        }

        // Top fırsatları göster (bu tick’teki tüm skorlar; çoğu Skip olabilir)
        println!("--- TOP {} (confidence) ---", engine::ranker::TOP_N);
        let top = ranker.top_n();
        if top.is_empty() {
            println!("  (boş — işlem gören piyasa yok, önce filtreleri kontrol edin)");
        } else {
            for (i, m) in top.iter().enumerate() {
                let q = id_to_question
                    .get(&m.market_id)
                    .map(|s| truncate(s, 48))
                    .unwrap_or_else(|| truncate(&m.market_id, 20));
                let ttr = id_to_ttr
                    .get(&m.market_id)
                    .copied()
                    .map(fmt_ttr_remaining)
                    .unwrap_or_else(|| "?".into());
                println!(
                    "  {:2}. conf={:.3} edge={:.3} {:<8?} | kalan ~{} | {}",
                    i + 1,
                    m.confidence,
                    m.edge_score,
                    m.decision,
                    ttr,
                    q
                );
            }
        }

        sleep(Duration::from_secs(TICK_SECS)).await;
    }
}
