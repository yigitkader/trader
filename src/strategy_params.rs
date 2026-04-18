//! Strateji eşikleri — `.env` ile ayarlanır; motor ve feature katmanı paylaşır.

use crate::types::DominantSignal;

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

fn env_u32(key: &str, default: u32) -> u32 {
    env_trim(key)
        .and_then(|s| s.parse().ok())
        .unwrap_or(default)
}

fn parse_dominant_allowlist(raw: Option<String>) -> (bool, bool, bool, bool) {
    // default: profit-focused proxy policy
    // FakeMove + Panic
    let Some(s) = raw else {
        return (true, false, true, false);
    };
    let mut fake = false;
    let mut abs = false;
    let mut panic = false;
    let mut mixed = false;

    for part in s.split(',') {
        let t = part.trim().to_ascii_lowercase();
        match t.as_str() {
            "fakemove" | "fake_move" | "fake" => fake = true,
            "absorption" | "abs" => abs = true,
            "panic" => panic = true,
            "mixed" => mixed = true,
            "all" | "*" => return (true, true, true, true),
            "" => {}
            other => {
                eprintln!(
                    "Uyarı: POLYMARKET_TRADE_DOMINANT_ALLOW içinde bilinmeyen değer: {other}"
                );
            }
        }
    }
    (fake, abs, panic, mixed)
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
    /// Skor öncesi: `raw *= (TTR_REF/ttr)^exp` — kısa vadeli piyasalara öncelik.
    pub scorer_ttr_scale_exp: f32,
    /// Ucuz YES + kısa TTR long-shot sömürüsü (NO tarafı).
    pub longshot_enabled: bool,
    pub longshot_yes_max: f32,
    pub longshot_ttr_max_secs: u64,
    pub longshot_raw_weight: f32,
    /// Çoklu sonuç: toplam mid bu kadar 1.0'dan düşükse underround arbitraj ipucu.
    pub multi_arb_sum_low: f32,
    pub multi_arb_sum_high: f32,

    // --- Trade policy filter (dominant signal) ---
    /// Dominant signal allowlist: FakeMove / Absorption / Panic / Mixed.
    /// Varsayılan: `FakeMove,Panic` (proxy forward-return analizine göre).
    pub allow_dom_fake_move: bool,
    pub allow_dom_absorption: bool,
    pub allow_dom_panic: bool,
    pub allow_dom_mixed: bool,
    /// Aynı market için aynı yönde tekrar trade etmeyi engellemek için basit TTL (sn).
    /// 0 = kapalı.

    // --- Signal thresholds (tape) ---
    /// fake_move: |momentum| bunun altındaysa 0 (tape fiyat birimi, 0..1 aralığı).
    pub fake_momentum_threshold: f32,
    /// fake_move: reaction_speed bunun altındaysa 0.
    pub fake_reaction_min: f32,
    /// panic: |momentum| bunun altındaysa 0.
    pub panic_spike_threshold: f32,

    // --- Signal thresholds (absorption) ---
    pub absorption_min_trades: u32,
    pub absorption_max_momentum: f32,
    pub absorption_pressure_deadband: f32,
    pub absorption_pressure_scale: f32,

    // --- Exit horizon (paper policy / future live exit) ---
    /// next-N observation horizon; dedup dostu. Default: 60 (proxy analizinde en iyi).
    pub exit_after_obs: u32,

    // --- Score inversion (contrarian mode) ---
    /// Ham skoru negatife cevir: FakeMove/Panic gibi sinyaller BuyYes yerine BuyNo uretir.
    /// Gercek outcome analizinde bu sinyaller ters yonde calistigindan varsayilan: true.
    pub score_invert: bool,
}

impl StrategyParams {
    pub fn load() -> Self {
        let _ = dotenvy::dotenv();
        let (allow_dom_fake_move, allow_dom_absorption, allow_dom_panic, allow_dom_mixed) =
            parse_dominant_allowlist(env_trim("POLYMARKET_TRADE_DOMINANT_ALLOW"));
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
            scorer_ttr_scale_exp: env_f32("POLYMARKET_SCORER_TTR_SCALE_EXP", 0.12),
            longshot_enabled: env_trim("POLYMARKET_LONGSHOT_ENABLED")
                .map(|s| matches!(s.to_ascii_lowercase().as_str(), "1" | "true" | "yes"))
                .unwrap_or(true),
            longshot_yes_max: env_f32("POLYMARKET_LONGSHOT_YES_MAX", 0.06),
            longshot_ttr_max_secs: env_u64("POLYMARKET_LONGSHOT_TTR_MAX_SECS", 259_200),
            longshot_raw_weight: env_f32("POLYMARKET_LONGSHOT_RAW_WEIGHT", 0.35),
            multi_arb_sum_low: env_f32("POLYMARKET_MULTI_ARB_SUM_LOW", 0.98),
            multi_arb_sum_high: env_f32("POLYMARKET_MULTI_ARB_SUM_HIGH", 1.02),

            allow_dom_fake_move,
            allow_dom_absorption,
            allow_dom_panic,
            allow_dom_mixed,

            // Tape thresholds: defaults lowered for L2 midpoint cadence (was too strict with ~2s ticks).
            fake_momentum_threshold: env_f32("POLYMARKET_FAKE_MOMENTUM_THRESHOLD", 0.002),
            fake_reaction_min: env_f32("POLYMARKET_FAKE_REACTION_MIN", 0.001),
            panic_spike_threshold: env_f32("POLYMARKET_PANIC_SPIKE_THRESHOLD", 0.004),

            absorption_min_trades: env_u32("POLYMARKET_ABSORPTION_MIN_TRADES", 6),
            absorption_max_momentum: env_f32("POLYMARKET_ABSORPTION_MAX_MOMENTUM", 0.01),
            absorption_pressure_deadband: env_f32("POLYMARKET_ABSORPTION_PRESSURE_DEADBAND", 0.01),
            absorption_pressure_scale: env_f32("POLYMARKET_ABSORPTION_PRESSURE_SCALE", 0.35),

            exit_after_obs: env_u32("POLYMARKET_EXIT_AFTER_OBS", 60),

            score_invert: env_trim("POLYMARKET_SCORE_INVERT")
                .map(|s| matches!(s.to_ascii_lowercase().as_str(), "1" | "true" | "yes"))
                .unwrap_or(false),
        }
    }
}

impl StrategyParams {
    pub fn dominant_allowed(&self, dom: &DominantSignal) -> bool {
        match dom {
            DominantSignal::FakeMove => self.allow_dom_fake_move,
            DominantSignal::Absorption => self.allow_dom_absorption,
            DominantSignal::Panic => self.allow_dom_panic,
            DominantSignal::Mixed => self.allow_dom_mixed,
        }
    }
}
