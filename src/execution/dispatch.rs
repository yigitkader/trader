use anyhow::Context;
use std::collections::HashMap;

use crate::execution::clob;
use crate::execution::config::{ExecutionConfig, ExecutionMode};
use crate::execution::pricing;
use crate::execution::sizing;
use crate::ingestion::book_feed::BookSnapshot;
use crate::types::{Decision, Market, ScoredMarket, now_secs};
use reqwest::Client;

#[derive(Debug, Clone)]
pub struct OrderPlan {
    pub decision: Decision,
    pub condition_id: String,
    pub question_short: String,
    pub token_id: Option<String>,
    pub reference_price_yes: f32,
    pub confidence: f32,
    pub edge_score: f32,
}

/// Per-session risk limits: cooldown, tick cap, open market cap, daily notional.
pub struct RiskGate {
    last_order_ts: HashMap<String, u64>,
    open_markets: HashMap<String, u64>,
    orders_this_tick: u16,
    daily_notional: f64,
    daily_reset_day: u32,
}

impl RiskGate {
    pub fn new() -> Self {
        Self {
            last_order_ts: HashMap::new(),
            open_markets: HashMap::new(),
            orders_this_tick: 0,
            daily_notional: 0.0,
            daily_reset_day: current_day(),
        }
    }

    pub fn begin_tick(&mut self) {
        self.orders_this_tick = 0;
        let today = current_day();
        if today != self.daily_reset_day {
            self.daily_notional = 0.0;
            self.daily_reset_day = today;
        }
    }

    fn can_order(&self, cfg: &ExecutionConfig, market_id: &str, notional: f64) -> Option<&'static str> {
        let now = now_secs();
        if let Some(&ts) = self.last_order_ts.get(market_id) {
            if now.saturating_sub(ts) < cfg.cooldown_per_market_secs {
                return Some("cooldown aktif");
            }
        }
        if self.orders_this_tick >= cfg.max_orders_per_tick {
            return Some("tick emir limiti doldu");
        }
        if self.open_markets.len() as u16 >= cfg.max_open_markets
            && !self.open_markets.contains_key(market_id)
        {
            return Some("açık pazar limiti doldu");
        }
        if self.daily_notional + notional > cfg.max_daily_notional {
            return Some("günlük notional limiti doldu");
        }
        None
    }

    fn record_order(&mut self, market_id: &str, notional: f64) {
        let now = now_secs();
        self.last_order_ts.insert(market_id.to_string(), now);
        self.open_markets.insert(market_id.to_string(), now);
        self.orders_this_tick += 1;
        self.daily_notional += notional;
    }
}

fn current_day() -> u32 {
    (now_secs() / 86_400) as u32
}

pub fn build_plan(market: &Market, scored: &ScoredMarket) -> Option<OrderPlan> {
    if matches!(scored.decision, Decision::Skip) {
        return None;
    }

    let token_id = match scored.decision {
        Decision::BuyYes => market.yes_token_id.clone(),
        Decision::BuyNo => market.no_token_id.clone(),
        Decision::Skip => None,
    };

    Some(OrderPlan {
        decision: scored.decision.clone(),
        condition_id: market.id.clone(),
        question_short: market.question.chars().take(80).collect(),
        token_id,
        reference_price_yes: market.yes_price,
        confidence: scored.confidence,
        edge_score: scored.edge_score,
    })
}

/// İşlem göreceği outcome token’ın L2 özeti (maker fiyat + Kelly referansı için).
pub fn book_snap_for_decision(
    market: &Market,
    decision: &Decision,
    books: &HashMap<String, BookSnapshot>,
) -> Option<BookSnapshot> {
    let tid = match decision {
        Decision::BuyYes => market.yes_token_id.as_ref()?,
        Decision::BuyNo => market.no_token_id.as_ref()?,
        Decision::Skip => return None,
    };
    books.get(tid).cloned()
}

pub async fn handle_signal(
    cfg: &ExecutionConfig,
    _http: &Client,
    market: &Market,
    scored: &ScoredMarket,
    tick: u64,
    risk: &mut RiskGate,
    book_snap: Option<BookSnapshot>,
) -> anyhow::Result<()> {
    let Some(plan) = build_plan(market, scored) else {
        return Ok(());
    };

    let limit = pricing::limit_price_buy(
        market,
        &plan.decision,
        cfg.price_slippage,
        cfg.order_style,
        book_snap.as_ref(),
    );

    let size = sizing::scaled_order_size(
        cfg.order_size,
        &plan.decision,
        plan.confidence,
        market,
        cfg.kelly_fraction,
        cfg.kelly_target,
        cfg.order_size_min,
        cfg.order_size_max,
    );

    let notional = size.to_string().parse::<f64>().unwrap_or(5.0) * limit as f64;

    if cfg.live_orders_enabled() {
        if let Some(reason) = risk.can_order(cfg, &plan.condition_id, notional) {
            println!(
                "[tick {tick}] [execution:BLOCKED] {:?} | {} | sebep: {}",
                plan.decision, plan.question_short, reason
            );
            return Ok(());
        }

        let order_id = clob::post_limit_buy(cfg, &plan, size, limit)
            .await
            .with_context(|| {
                format!(
                    "CLOB limit alım (tick {tick}, condition {})",
                    plan.condition_id
                )
            })?;

        risk.record_order(&plan.condition_id, notional);

        println!(
            "[tick {tick}] [execution:LIVE] {:?} | {} | order_id={} | limit≈{:.4} sz={} {:?} | edge={:.4} ann={:.3} conf={:.3}",
            plan.decision,
            plan.question_short,
            order_id,
            limit,
            size,
            cfg.order_style,
            plan.edge_score,
            scored.annualized_edge,
            plan.confidence
        );
        return Ok(());
    }

    let token = plan
        .token_id
        .as_deref()
        .map(short_id)
        .unwrap_or_else(|| "YOK (Gamma/CLOB token eksik)".into());

    println!(
        "[tick {tick}] [execution:DRY-RUN] {:?} | {} | yes_mid={:.4} | edge={:.4} ann={:.2} conf={:.3} | limit≈{:.4} sz={} {:?} | token={}",
        plan.decision,
        plan.question_short,
        plan.reference_price_yes,
        plan.edge_score,
        scored.annualized_edge,
        plan.confidence,
        limit,
        size,
        cfg.order_style,
        token
    );

    if matches!(cfg.mode, ExecutionMode::Live) {
        if !cfg.l2_credentials_ready() {
            println!("         └─ LIVE istendi ama L2 credential eksik — yalnızca dry-run");
        } else if !cfg.live_trading {
            println!(
                "         └─ LIVE + L2 hazır; gerçek POST için POLYMARKET_LIVE_TRADING=1 ve POLYMARKET_PRIVATE_KEY gerekir"
            );
        } else if cfg.private_key.is_none() {
            println!("         └─ POLYMARKET_LIVE_TRADING açık ama private key yok");
        }
    }

    Ok(())
}

fn short_id(s: &str) -> String {
    let n = s.len();
    if n <= 24 {
        return s.to_string();
    }
    format!("{}…{} (len={})", &s[..12], &s[n - 6..], n)
}
