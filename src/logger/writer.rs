use crate::types::LogEntry;
use std::fs::OpenOptions;
use std::io::Write;

const LOG_FILE: &str = "signals.jsonl";

// Her satır bir JSON → kalibrasyon için kolayca parse edilir
pub fn write(entry: &LogEntry) -> anyhow::Result<()> {
    let line = serde_json::to_string(entry)?;

    let mut file = OpenOptions::new()
        .create(true)
        .append(true)
        .open(LOG_FILE)?;

    writeln!(file, "{}", line)?;
    Ok(())
}