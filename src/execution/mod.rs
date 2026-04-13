//! CLOB: `.env` ile dry-run veya (LIVE + `POLYMARKET_LIVE_TRADING`) limit alım POST'u.

pub mod config;
mod clob;
mod dispatch;

pub use config::ExecutionConfig;
pub use dispatch::{handle_signal, RiskGate};
