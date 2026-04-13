//! İleride CLOB alım/satım: şimdilik `.env` + dry-run plan çıktısı.

pub mod config;
mod dispatch;

pub use config::ExecutionConfig;
pub use dispatch::handle_signal;
