//! Strateji eşikleri — `.env` ile ayarlanır; motor ve feature katmanı paylaşır.

fn env_trim(key: &str) -> Option<String> {
    std::env::var(key)
        .ok()
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
}

fn env_f32(key: &str, default: f32) -> f32 {
    env_trim(key)
        .and_then(|s| s.parse::<f32>().ok())
        .filter(|v| v.is_finite())
        .unwrap_or(default)
}

fn env_u64(key: &str, default: u64) -> u64 {
    env_trim(key)
        .and_then(|s| s.parse().ok())
        .unwrap_or(default)
}

fn env_usize(key: &str, default: usize) -> usize {
    env_trim(key)
        .and_then(|s| s.parse().ok())
        .unwrap_or(default)
}

/// Eşikleri grid search / backtest ile optimize etmek için `signals.jsonl` veya tick arşivini
/// dışarıda (ör. Python) kullan; burada yalnızca `.env` üzerinden kalibre edilir.
#[derive(Debug, Clone)]
pub struct StrategyParams {
    pub min_edge: f32,
    pub min_outcome_mid: f32,
    pub max_outcome_mid: f32,
    pub dominant_mixed_max: f32,
    pub dominant_tie_eps: f32,
    pub time_decay_horizon_hours: f32,
    /// TTR cezası: `gerekli_edge = min_edge * (ttr/ref)^exponent` (uzun vadelerde daha yüksek bar).
    pub ttr_edge_ref_secs: u64,
    pub ttr_edge_exponent: f32,
    /// Bu tick'te L2 çekilecek en fazla benzersiz token (0 = kitap özelliği kapalı).
    pub book_max_tokens_per_tick: usize,
    /// Imbalance için bid/ask tarafında toplanan seviye sayısı.
    pub book_depth_levels: usize,
}

impl StrategyParams {
    pub fn load() -> Self {
        let _ = dotenvy::dotenv();
        Self {
            min_edge: env_f32("POLYMARKET_MIN_EDGE", 0.025),
            min_outcome_mid: env_f32("POLYMARKET_MIN_OUTCOME_MID", 0.03),
            max_outcome_mid: env_f32("POLYMARKET_MAX_OUTCOME_MID", 0.97),
            dominant_mixed_max: env_f32("POLYMARKET_DOMINANT_MIXED_MAX", 0.05),
            dominant_tie_eps: env_f32("POLYMARKET_DOMINANT_TIE_EPS", 0.02),
            time_decay_horizon_hours: env_f32("POLYMARKET_TIME_DECAY_HORIZON_HOURS", 48.0),
            ttr_edge_ref_secs: env_u64("POLYMARKET_TTR_EDGE_REF_SECS", 86_400),
            ttr_edge_exponent: env_f32("POLYMARKET_TTR_EDGE_EXPONENT", 0.5),
            book_max_tokens_per_tick: env_usize("POLYMARKET_BOOK_MAX_TOKENS", 120),
            book_depth_levels: env_usize("POLYMARKET_BOOK_DEPTH_LEVELS", 5).max(1),
        }
    }
}
