//! Binary outcome için Kelly payı → emir büyüklüğü; isteğe bağlı kasa (`BANKROLL_USDC`) ile %1–%10 aralığı.

use std::str::FromStr as _;

use rust_decimal::Decimal;

use crate::execution::config::ExecutionConfig;
use crate::types::{Decision, Market};

/// Uzun taraf: maliyet `cost` iken kazanma olasılığı `p` (0..1). Pay ≈ (p−c)/(1−c).
pub fn kelly_fraction_long(p: f32, cost: f32) -> f32 {
    if !p.is_finite() || !cost.is_finite() {
        return 0.0;
    }
    if p <= cost || cost <= 0.01 || cost >= 0.99 {
        return 0.0;
    }
    (p - cost) / (1.0 - cost).clamp(0.02, 0.99)
}

/// `POLYMARKET_BANKROLL_USDC` doluysa notional = kasa × [min_frac,max_frac] arası güç; aksi halde baz lot × Kelly çarpanı.
pub fn scaled_order_size(
    cfg: &ExecutionConfig,
    decision: &Decision,
    confidence: f32,
    market: &Market,
    limit_px: f32,
) -> Decimal {
    let lp = (limit_px as f64).clamp(0.01, 0.99);

    let (p, cost) = match decision {
        Decision::BuyYes => (confidence, market.yes_price),
        Decision::BuyNo => ((1.0 - confidence).clamp(0.0, 1.0), market.no_price),
        Decision::Skip => {
            let mut s = cfg.order_size;
            if s < cfg.order_size_min {
                s = cfg.order_size_min;
            }
            if s > cfg.order_size_max {
                s = cfg.order_size_max;
            }
            return s;
        }
    };

    let k_raw = kelly_fraction_long(p, cost) * cfg.kelly_fraction.clamp(0.0, 1.0);

    if let Some(bankroll) = cfg.bankroll_usdc.filter(|b| b.is_finite() && *b > 0.0) {
        let kappa = (k_raw / cfg.kelly_target.max(0.005)).clamp(0.0, 1.0);
        let conf_pull = ((confidence - 0.5).abs() * 2.0).clamp(0.0, 1.0);
        let strength = (0.55 * kappa + 0.45 * conf_pull).clamp(0.0, 1.0);
        let minf = cfg.bankroll_min_frac.clamp(0.001, 0.5) as f64;
        let maxf = cfg
            .bankroll_max_frac
            .max(cfg.bankroll_min_frac)
            .clamp(0.002, 1.0) as f64;
        let frac = minf + (maxf - minf) * f64::from(strength);
        let usd = bankroll * frac;
        let shares = usd / lp;
        let s = format!("{:.6}", shares.max(0.0));
        let mut sz = Decimal::from_str(&s).unwrap_or(cfg.order_size);
        if sz < cfg.order_size_min {
            sz = cfg.order_size_min;
        }
        if sz > cfg.order_size_max {
            sz = cfg.order_size_max;
        }
        return sz;
    }

    let target = cfg.kelly_target.max(0.005);
    let mult = (k_raw / target).clamp(0.2f32, 2.5f32);
    let m = Decimal::try_from(mult as f64).unwrap_or(Decimal::ONE);
    let mut sz = cfg.order_size * m;
    if sz < cfg.order_size_min {
        sz = cfg.order_size_min;
    }
    if sz > cfg.order_size_max {
        sz = cfg.order_size_max;
    }
    sz
}
