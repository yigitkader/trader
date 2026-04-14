//! CLOB: `.env` ile dry-run veya (LIVE + `POLYMARKET_LIVE_TRADING`) limit alım POST'u.

pub mod config;
mod clob;
mod dispatch;
mod pricing;
mod sizing;

pub use config::ExecutionConfig;
pub use dispatch::{book_snap_for_decision, handle_signal, RiskGate};
