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
    /// Bu tick’teki WS trade sayısı (pencere boyutu üst sınırlı).
    #[serde(default)]
    pub trade_count: u32,
    /// L2 bid hacmi / (bid+ask) üst seviyelerde; kitap yoksa 0.5.
    #[serde(default = "default_half")]
    pub orderbook_imbalance: f32,
}

fn default_half() -> f32 {
    0.5
}

// ─── SIGNALS ────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SignalSet {
    pub market_id: String,
    pub fake_move: f32,
    pub absorption: f32,
    pub panic: f32,
    /// L2 dengesizliğinden türetilen işaretli skor (−1..1).
    #[serde(default)]
    pub book_skew: f32,
}

// ─── ENGINE OUTPUT ──────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ScoredMarket {
    pub market_id: String,
    pub confidence: f32,
    pub edge_score: f32,
    /// Basit yıllıklandırma: `edge_score / max(ttr_yıl, ~1s)`.
    #[serde(default)]
    pub annualized_edge: f32,
    pub decision: Decision,
    pub dominant_signal: DominantSignal,
}

impl PartialEq for ScoredMarket {
    fn eq(&self, other: &Self) -> bool {
        self.confidence.total_cmp(&other.confidence).is_eq()
    }
}

impl Eq for ScoredMarket {}

impl PartialOrd for ScoredMarket {
    fn partial_cmp(&self, other: &Self) -> Option<std::cmp::Ordering> {
        Some(self.cmp(other))
    }
}

impl Ord for ScoredMarket {
    fn cmp(&self, other: &Self) -> std::cmp::Ordering {
        self.confidence.total_cmp(&other.confidence)
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
    #[serde(default)]
    pub annualized_edge: f32,
    pub decision: Decision,
    pub dominant_signal: DominantSignal,
    pub features_snapshot: Features,
    /// Filled when a live order is placed.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub order_id: Option<String>,
    /// Limit price used for the order.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub limit_price: Option<f32>,
}

// ─── HELPERS ────────────────────────────────────────────

pub fn now_secs() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_secs()
}
