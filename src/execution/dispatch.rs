use crate::execution::config::{ExecutionConfig, ExecutionMode};
use crate::types::{Decision, Market, ScoredMarket};
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

/// Sinyal sonrası çağrılır: dry-run’da planı yazar; live’da henüz `POST` yok.
pub async fn handle_signal(
    cfg: &ExecutionConfig,
    _http: &Client,
    market: &Market,
    scored: &ScoredMarket,
    tick: u64,
) -> anyhow::Result<()> {
    let Some(plan) = build_plan(market, scored) else {
        return Ok(());
    };

    if cfg.live_orders_enabled() {
        let _ = (
            cfg.api_key.as_ref(),
            cfg.api_secret.as_ref(),
            cfg.api_passphrase.as_ref(),
        );
        anyhow::bail!(
            "LIVE emir gönderimi henüz bağlanmadı: CLOB order build + EIP-712 imza + POST /order burada eklenecek (tick {tick}, condition {})",
            plan.condition_id
        );
    }

    let token = plan
        .token_id
        .as_deref()
        .map(short_id)
        .unwrap_or_else(|| "YOK (Gamma/CLOB token eksik)".into());

    println!(
        "[tick {tick}] [execution:DRY-RUN] {:?} | {} | yes_mid={:.4} | conf={:.3} edge={:.3} | token={}",
        plan.decision,
        plan.question_short,
        plan.reference_price_yes,
        plan.confidence,
        plan.edge_score,
        token
    );

    if matches!(cfg.mode, ExecutionMode::Live) {
        if !cfg.l2_credentials_ready() {
            println!("         └─ LIVE istendi ama L2 credential eksik — yalnızca dry-run");
        } else {
            println!(
                "         └─ LIVE + L2 hazır; gerçek POST için execution/config.rs içinde CLOB_ORDER_DISPATCH_IMPLEMENTED + dispatch"
            );
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
