use serde::{Deserialize, Serialize};
use std::time::{SystemTime, UNIX_EPOCH};

// ─── RAW DATA ───────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Market {
    pub id: String,
    pub question: String,
    pub yes_price: f32,
    pub no_price: f32,
    /// Gamma `volumeNum` (CLOB `/markets` artık hacim döndürmüyor).
    pub volume: Option<f64>,
    pub spread: f32,
    pub time_to_resolution: u64,
    pub market_type: MarketType,
    /// CLOB YES outcome token — `ws/market` aboneliği için (condition_id değil).
    pub yes_token_id: Option<String>,
    /// CLOB NO outcome token — emir tarafı için.
    pub no_token_id: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum MarketType {
    Binary,
    MultiOutcome,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RawTrade {
    pub market_id: String,
    pub side: TradeSide,
    pub price: f32,
    pub size: f64,
    /// Kaynak: WS `timestamp` (çoğunlukla milisaniye unix).
    pub timestamp: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum TradeSide {
    Buy,
    Sell,
}

// ─── FEATURES ───────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Features {
    pub market_id: String,
    pub momentum: f32,
    pub pressure: f32,
    pub reaction_speed: f32,
    pub time_decay: f32,
}

// ─── SIGNALS ────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SignalSet {
    pub market_id: String,
    pub fake_move: f32,
    pub absorption: f32,
    pub panic: f32,
}

// ─── ENGINE OUTPUT ──────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ScoredMarket {
    pub market_id: String,
    pub confidence: f32,
    pub edge_score: f32,
    pub decision: Decision,
    pub dominant_signal: DominantSignal,
}

impl PartialEq for ScoredMarket {
    fn eq(&self, other: &Self) -> bool {
        self.confidence
            .partial_cmp(&other.confidence)
            .unwrap()
            .is_eq()
    }
}

impl Eq for ScoredMarket {}

impl PartialOrd for ScoredMarket {
    fn partial_cmp(&self, other: &Self) -> Option<std::cmp::Ordering> {
        self.confidence.partial_cmp(&other.confidence)
    }
}

impl Ord for ScoredMarket {
    fn cmp(&self, other: &Self) -> std::cmp::Ordering {
        self.partial_cmp(other).unwrap_or(std::cmp::Ordering::Equal)
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum Decision {
    BuyYes,
    BuyNo,
    Skip,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum DominantSignal {
    FakeMove,
    Absorption,
    Panic,
    Mixed,
}

// ─── LOG ────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LogEntry {
    pub timestamp: u64,
    pub market_id: String,
    pub market_question: String,
    pub price_at_signal: f32,
    pub confidence: f32,
    pub edge_score: f32,
    pub decision: Decision,
    pub dominant_signal: DominantSignal,
    pub features_snapshot: Features,
}

// ─── HELPERS ────────────────────────────────────────────

pub fn now_secs() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_secs()
}
