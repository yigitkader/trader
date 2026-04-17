mod engine;
mod execution;
mod features;
mod http_client;
mod ingestion;
mod logger;
mod signals;
mod strategy_params;
mod types;

use ingestion::trade_stream::SharedTradeBuffers;
use std::collections::{HashMap, HashSet, VecDeque};
use std::hash::{Hash, Hasher};
use std::sync::Arc;
use tokio::sync::Mutex;
use tokio::time::{sleep, Duration};
use types::{now_secs, RawTrade};

#[derive(Clone)]
struct PaperPosition {
    decision: types::Decision,
    entry_tape_price: f32,
    obs: u32,
}

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
    let truncated: String = t.chars().take(max.saturating_sub(3)).collect();
    format!("{truncated}...")
}

#[tokio::main]
async fn main() {
    let _ = rustls::crypto::ring::default_provider().install_default();
    let exec_cfg = execution::ExecutionConfig::load();
    let strategy = strategy_params::StrategyParams::load();
    let client = http_client::build().expect("HTTP client (reqwest) kurulamadı");
    let mut price_windows: HashMap<String, VecDeque<(u64, f32)>> = HashMap::new();
    let trade_buffers: SharedTradeBuffers = Arc::new(Mutex::new(HashMap::new()));
    let mut ranker = engine::ranker::Ranker::new();
    // Tape momentumu: mümkünse CLOB L2 midpoint, yoksa Gamma yes_price.
    let mut prev_tape_price: HashMap<String, f32> = HashMap::new();
    // Aynı fingerprint tekrar tekrar loglanmasın diye basit dedup.
    let mut last_log_fp: HashMap<String, u64> = HashMap::new();
    let mut last_log_ts: HashMap<String, u64> = HashMap::new();
    // Paper pozisyonlar (dry-run / analiz için): entry + observation sayacı.
    let mut paper_pos: HashMap<String, PaperPosition> = HashMap::new();

    let risk_gate = Arc::new(tokio::sync::Mutex::new(execution::RiskGate::new()));

    let clob_session: Option<Arc<execution::ClobSession>> =
        if exec_cfg.live_orders_enabled() {
            match execution::ClobSession::connect(&exec_cfg).await {
                Ok(s) => {
                    println!("CLOB oturumu hazır (tek auth, canlı emirler paylaşır)");
                    Some(Arc::new(s))
                }
                Err(e) => {
                    eprintln!("CLOB auth başarısız — canlı emirler atılamaz: {e:#}");
                    None
                }
            }
        } else {
            None
        };

    let hub_tx = ingestion::trade_stream::spawn_trade_hub(Arc::clone(&trade_buffers));
    let mut had_ws_asset_subscriptions = false;
    let mut tick: u64 = 0;

    println!(
        "Polymarket bot başladı — analiz + execution katmanı ({})",
        exec_cfg.describe()
    );
    let max_ttr = ingestion::market_meta::max_time_to_resolution_secs();
    println!(
        "Filtreler: TTR ≥ {}s | spread ≤ {:.2} | hacim ≥ {:.0} (Gamma) | min_edge>{:.3} (TTR ile ölçeklenir){}",
        ingestion::market_meta::MIN_TTR_SECS,
        ingestion::market_meta::MAX_SPREAD,
        ingestion::market_meta::MIN_VOLUME,
        strategy.min_edge,
        max_ttr.map_or(String::new(), |mx| format!(
            " | max TTR ≤ {}s (~{})",
            mx,
            fmt_ttr_remaining(mx)
        ))
    );

    loop {
        tick += 1;
        ranker.clear();
        {
            let mut g = risk_gate.lock().await;
            g.begin_tick();
        }

        // 1. Marketleri çek
        let (markets, mut multi_arb_hints) = match ingestion::price_feed::fetch_markets(
            &client,
            strategy.multi_arb_sum_low,
            strategy.multi_arb_sum_high,
        )
        .await
        {
            Ok(m) => m,
            Err(e) => {
                eprintln!("[tick {}] fetch error: {}", tick, e);
                sleep(Duration::from_secs(TICK_SECS)).await;
                continue;
            }
        };

        if !multi_arb_hints.is_empty() {
            multi_arb_hints.sort_by(|a, b| {
                (b.sum_prices - 1.0)
                    .abs()
                    .total_cmp(&(a.sum_prices - 1.0).abs())
            });
            println!(
                "[tick {}] Çoklu-sonuç arbitraj ipuçları (otomatik emir yok): {} adet",
                tick,
                multi_arb_hints.len()
            );
            for h in multi_arb_hints.iter().take(8) {
                println!(
                    "  {:?} | Σmid={:.4} (Δ{:+.4}) | n={} | {} | {}",
                    h.kind,
                    h.sum_prices,
                    h.sum_prices - 1.0,
                    h.n_outcomes,
                    truncate(&h.condition_id, 14),
                    truncate(&h.question, 56)
                );
            }
        }

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
        price_windows.retain(|id, _| tradeable_market_ids.contains(id));

        let trade_snapshot: HashMap<String, VecDeque<RawTrade>> = {
            let g = trade_buffers.lock().await;
            tradeable_market_ids
                .iter()
                .filter_map(|id| g.get(id).map(|buf| (id.clone(), buf.clone())))
                .collect()
        };
        let trade_buffer_stats = (
            trade_snapshot.len(),
            trade_snapshot.values().map(|b| b.len()).sum::<usize>(),
        );

        let book_snapshots: HashMap<String, ingestion::book_feed::BookSnapshot> =
            if strategy.book_max_tokens_per_tick > 0 {
                let mut book_tokens: Vec<String> = Vec::new();
                for m in markets
                    .iter()
                    .filter(|m| ingestion::market_meta::is_tradeable(m))
                {
                    if let Some(ref t) = m.yes_token_id {
                        book_tokens.push(t.clone());
                    }
                    if let Some(ref t) = m.no_token_id {
                        book_tokens.push(t.clone());
                    }
                }
                book_tokens.sort();
                book_tokens.dedup();
                book_tokens.truncate(strategy.book_max_tokens_per_tick);
                ingestion::book_feed::fetch_snapshots(
                    &exec_cfg.clob_base,
                    &book_tokens,
                    strategy.book_depth_levels,
                )
                .await
            } else {
                HashMap::new()
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

            // 3. Price window güncelle (tape fiyatı)
            let window = price_windows
                .entry(market.id.clone())
                .or_insert_with(VecDeque::new);

            // 4. Trade buffer (merkezi WS hub doldurur; key = condition_id / `market` alanı)
            let trades: VecDeque<RawTrade> = trade_snapshot
                .get(&market.id)
                .cloned()
                .unwrap_or_default();

            let yes_book = market
                .yes_token_id
                .as_ref()
                .and_then(|tid| book_snapshots.get(tid));

            let tape_price = yes_book
                .and_then(|b| match (b.best_bid, b.best_ask) {
                    (Some(bb), Some(ba)) if ba > bb => Some((bb + ba) * 0.5),
                    _ => None,
                })
                .unwrap_or(market.yes_price);

            features::momentum::push_price(window, now, tape_price);

            // 5. Features hesapla
            let gamma_tick_delta = prev_tape_price
                .get(&market.id)
                .map(|p| tape_price - *p)
                .unwrap_or(0.0);
            let feats = features::compute_all(
                market,
                window,
                &trades,
                &strategy,
                yes_book,
                gamma_tick_delta,
            );
            prev_tape_price.insert(market.id.clone(), tape_price);

            // 6. Sinyaller
            let sigs = signals::compute_all(&feats, &strategy);

            // 7. Engine
            let scored = engine::process(&sigs, market, &strategy);

            max_conf = max_conf.max(scored.confidence);
            max_edge = max_edge.max(scored.edge_score.abs());

            // 8. Sadece anlamlı sinyalleri logla
            if !matches!(scored.decision, types::Decision::Skip) {
                non_skip += 1;
                let strength_max = sigs
                    .fake_move
                    .abs()
                    .max(sigs.absorption.abs())
                    .max(sigs.panic.abs());
                let entry = types::LogEntry {
                    log_schema: types::LOG_ENTRY_SCHEMA_VERSION,
                    timestamp: now,
                    market_id: market.id.clone(),
                    market_question: market.question.clone(),
                    price_at_signal: market.yes_price,
                    tape_price,
                    confidence: scored.confidence,
                    edge_score: scored.edge_score,
                    annualized_edge: scored.annualized_edge,
                    decision: scored.decision.clone(),
                    dominant_signal: scored.dominant_signal.clone(),
                    features_snapshot: feats.clone(),
                    signal_snapshot: types::SignalSnapshot {
                        fake_move: sigs.fake_move,
                        absorption: sigs.absorption,
                        panic: sigs.panic,
                        book_skew: sigs.book_skew,
                        strength_max,
                    },
                    dominance_params: types::DominanceParamsSnapshot {
                        dominant_mixed_max: strategy.dominant_mixed_max,
                        dominant_tie_eps: strategy.dominant_tie_eps,
                    },
                    labels: types::LogLabels {
                        schema_version: types::LOG_LABEL_SCHEMA_VERSION,
                        outcome_yes: None,
                        forward_return_yes: None,
                    },
                    order_id: None,
                    limit_price: None,
                };

                // Per-market fingerprint: küçük oynaklıklar için yuvarla, spam log'u engelle.
                let fp = {
                    let mut h = std::collections::hash_map::DefaultHasher::new();
                    entry.market_id.hash(&mut h);
                    entry.decision.hash(&mut h);
                    entry.dominant_signal.hash(&mut h);
                    // Fiyat ve sinyaller: gürültüye dayanıklı quantize.
                    ((entry.price_at_signal * 10_000.0) as i64).hash(&mut h);
                    ((entry.confidence * 10_000.0) as i64).hash(&mut h);
                    ((entry.edge_score * 10_000.0) as i64).hash(&mut h);
                    ((entry.signal_snapshot.fake_move * 10_000.0) as i64).hash(&mut h);
                    ((entry.signal_snapshot.absorption * 10_000.0) as i64).hash(&mut h);
                    ((entry.signal_snapshot.panic * 10_000.0) as i64).hash(&mut h);
                    ((entry.signal_snapshot.book_skew * 10_000.0) as i64).hash(&mut h);
                    h.finish()
                };
                let last_fp = last_log_fp.get(&entry.market_id).copied();
                let last_ts = last_log_ts.get(&entry.market_id).copied().unwrap_or(0);
                // Aynı fingerprint ise 60sn içinde tekrar yazma.
                let should_write = last_fp.map_or(true, |p| p != fp) || now.saturating_sub(last_ts) >= 60;
                if should_write {
                    if let Err(e) = logger::writer::write(&entry) {
                        eprintln!("[tick {}] log error: {}", tick, e);
                    } else {
                        last_log_fp.insert(entry.market_id.clone(), fp);
                        last_log_ts.insert(entry.market_id.clone(), now);
                    }
                }

                println!(
                    "[tick {}] [SINYAL] {} | yes {:.2} | conf {:.2} | edge {:.3} ann {:.2} | {:?} | {:?} | mom {:.2} press {:.2} ob {:.2}",
                    tick,
                    truncate(&market.question, 70),
                    market.yes_price,
                    scored.confidence,
                    scored.edge_score,
                    scored.annualized_edge,
                    scored.decision,
                    scored.dominant_signal,
                    feats.momentum,
                    feats.pressure,
                    feats.orderbook_imbalance
                );

                // --- Paper exit policy: next-N observation horizon (proxy analiz: next60 en iyi) ---
                // Not: Bu sadece dry-run / analiz içindir. Live exit (SELL) daha sonra bu state'e bağlanacak.
                if !exec_cfg.live_orders_enabled() {
                    match scored.decision {
                        types::Decision::BuyYes | types::Decision::BuyNo => {
                            let e = paper_pos.entry(market.id.clone()).or_insert(PaperPosition {
                                decision: scored.decision.clone(),
                                entry_tape_price: tape_price,
                                obs: 0,
                            });
                            // Eğer yön değiştiyse "flip exit" gibi davran: pozisyonu kapatıp yeni entry başlat.
                            if e.decision != scored.decision {
                                let pnl = match e.decision {
                                    types::Decision::BuyYes => tape_price - e.entry_tape_price,
                                    types::Decision::BuyNo => e.entry_tape_price - tape_price,
                                    types::Decision::Skip => 0.0,
                                };
                                println!(
                                    "[tick {tick}] [PAPER:EXIT-FLIP] {:?} -> {:?} | {} | Δtape={:+.4} over {} obs",
                                    e.decision,
                                    scored.decision,
                                    truncate(&market.question, 56),
                                    pnl,
                                    e.obs
                                );
                                *e = PaperPosition {
                                    decision: scored.decision.clone(),
                                    entry_tape_price: tape_price,
                                    obs: 0,
                                };
                            } else {
                                e.obs = e.obs.saturating_add(1);
                                if e.obs >= strategy.exit_after_obs {
                                    let pnl = match e.decision {
                                        types::Decision::BuyYes => tape_price - e.entry_tape_price,
                                        types::Decision::BuyNo => e.entry_tape_price - tape_price,
                                        types::Decision::Skip => 0.0,
                                    };
                                    println!(
                                        "[tick {tick}] [PAPER:EXIT-HORIZON] {:?} | {} | Δtape={:+.4} over {} obs (exit_after_obs={})",
                                        e.decision,
                                        truncate(&market.question, 56),
                                        pnl,
                                        e.obs,
                                        strategy.exit_after_obs
                                    );
                                    paper_pos.remove(&market.id);
                                }
                            }
                        }
                        _ => {}
                    }
                }

                let snap =
                    execution::book_snap_for_decision(market, &scored.decision, &book_snapshots);

                if exec_cfg.live_orders_enabled() {
                    if let Some(clob_spawn) = clob_session.clone() {
                        let risk = Arc::clone(&risk_gate);
                        let cfg = exec_cfg.clone();
                        let client_spawn = client.clone();
                        let market_spawn = market.clone();
                        let scored_spawn = scored.clone();
                        tokio::spawn(async move {
                            if let Err(e) = execution::handle_signal(
                                &cfg,
                                &client_spawn,
                                &market_spawn,
                                &scored_spawn,
                                tick,
                                risk.as_ref(),
                                Some(clob_spawn.as_ref()),
                                snap,
                            )
                            .await
                            {
                                eprintln!("[tick {}] execution error: {:#}", tick, e);
                            }
                        });
                    }
                } else {
                    if let Err(e) = execution::handle_signal(
                        &exec_cfg,
                        &client,
                        market,
                        &scored,
                        tick,
                        risk_gate.as_ref(),
                        None,
                        snap,
                    )
                    .await
                    {
                        eprintln!("[tick {}] execution error: {:#}", tick, e);
                    }
                }

                ranker.push(scored);
            }
        }

        let fetched_ids: HashSet<String> = markets.iter().map(|m| m.id.clone()).collect();
        prev_tape_price.retain(|id, _| fetched_ids.contains(id));
        last_log_fp.retain(|id, _| fetched_ids.contains(id));
        last_log_ts.retain(|id, _| fetched_ids.contains(id));
        paper_pos.retain(|id, _| fetched_ids.contains(id));

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

        // Top fırsatlar: yalnızca Skip olmayan (fiyat kapısı + eşik geçen) adaylar
        println!(
            "--- TOP {} (|yıllıklandırılmış edge|) ---",
            engine::ranker::TOP_N
        );
        let top = ranker.top_n();
        if top.is_empty() {
            println!(
                "  (boş — bu tick’te Skip dışı karar yok: conf eşiği veya fiyat kapısı / sinyal yok)"
            );
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
                    "  {:2}. conf={:.3} edge={:.3} ann={:.3} {:<8?} | kalan ~{} | {}",
                    i + 1,
                    m.confidence,
                    m.edge_score,
                    m.annualized_edge,
                    m.decision,
                    ttr,
                    q
                );
            }
        }

        sleep(Duration::from_secs(TICK_SECS)).await;
    }
}
