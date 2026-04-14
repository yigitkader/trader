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

/// Gamma’da 3+ sonuçlu piyasalarda fiyat toplamının 1.0’dan sapması (yalnızca tespit).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MultiArbKind {
    /// Toplam 1’den küçük: teorik Dutch-book (tüm YES ucuz).
    Underround,
    /// Toplam 1’den büyük: kitap pahalı.
    Overround,
}

#[derive(Debug, Clone)]
pub struct MultiOutcomeArbHint {
    pub condition_id: String,
    pub question: String,
    pub n_outcomes: usize,
    pub sum_prices: f32,
    pub kind: MultiArbKind,
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
    /// Bir önceki tick’teki Gamma YES mid ile fark (tape sinyalleri için; 5 dk momentum 0 iken bile hareket yakalar).
    #[serde(default)]
    pub gamma_tick_delta: f32,
    pub pressure: f32,
    pub reaction_speed: f32,
    pub time_decay: f32,
    /// Bu tick’teki WS trade sayısı (pencere boyutu üst sınırlı).
    #[serde(default)]
    pub trade_count: u32,
    /// L2 bid hacmi / (bid+ask) üst seviyelerde; kitap yoksa 0.5.
    #[serde(default = "default_half")]
    pub orderbook_imbalance: f32,
    #[serde(default = "default_half")]
    pub orderbook_imbalance_weighted: f32,
    #[serde(default)]
    pub orderbook_spread_l2: f32,
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

#[derive(Debug, Clone, Serialize, Deserialize, Hash)]
pub enum Decision {
    BuyYes,
    BuyNo,
    Skip,
}

#[derive(Debug, Clone, Serialize, Deserialize, Hash)]
pub enum DominantSignal {
    FakeMove,
    Absorption,
    Panic,
    Mixed,
}

// ─── LOG ────────────────────────────────────────────────

/// `LogLabels` sürümü; offline araçlar uyumluluk için kontrol eder.
pub const LOG_LABEL_SCHEMA_VERSION: u32 = 1;

/// `log_schema` alanı — ham sinyal + dominance snapshot içeren satırlar.
pub const LOG_ENTRY_SCHEMA_VERSION: u32 = 1;

/// Çözüm / forward-return gibi kalibrasyon etiketleri — bot yazarken boş, sonra `merge_outcome_labels` vb. doldurur.
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct LogLabels {
    #[serde(default)]
    pub schema_version: u32,
    /// Pazar YES olarak kapandı mı (`true` = YES kazandı).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub outcome_yes: Option<bool>,
    /// Sinyal anından sonra YES mid oransal değişimi (ör. (p1-p0)/p0); offline hesaplanabilir.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub forward_return_yes: Option<f32>,
}

/// Log satırı anındaki ham sinyaller (`book_skew` scorer’da ayrı ağırlıkla).
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct SignalSnapshot {
    pub fake_move: f32,
    pub absorption: f32,
    pub panic: f32,
    pub book_skew: f32,
    /// max(|fake_move|, |absorption|, |panic|) — `DominantSignal::Mixed` ile kıyas için.
    pub strength_max: f32,
}

/// `dominant_signal` hesabında kullanılan eşikler (log anı; `.env` ile sonradan değişse bile satır self-contained).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DominanceParamsSnapshot {
    #[serde(default = "default_dominant_mixed_max")]
    pub dominant_mixed_max: f32,
    #[serde(default = "default_dominant_tie_eps")]
    pub dominant_tie_eps: f32,
}

fn default_dominant_mixed_max() -> f32 {
    0.05
}

fn default_dominant_tie_eps() -> f32 {
    0.02
}

impl Default for DominanceParamsSnapshot {
    fn default() -> Self {
        Self {
            dominant_mixed_max: default_dominant_mixed_max(),
            dominant_tie_eps: default_dominant_tie_eps(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LogEntry {
    /// 0 = eski satır (signal_snapshot yok sayılmamalı); 1 = tam şema.
    #[serde(default)]
    pub log_schema: u32,
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
    #[serde(default)]
    pub signal_snapshot: SignalSnapshot,
    #[serde(default)]
    pub dominance_params: DominanceParamsSnapshot,
    #[serde(default)]
    pub labels: LogLabels,
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
