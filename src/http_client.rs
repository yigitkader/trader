use reqwest::Client;
use std::time::Duration;

/// Tek `Client`: connection pool, sıkıştırma (Cargo feature), zaman aşımı.
pub fn build() -> reqwest::Result<Client> {
    Client::builder()
        .user_agent(concat!(
            "trader/",
            env!("CARGO_PKG_VERSION"),
            " (https://polymarket.com)"
        ))
        .connect_timeout(Duration::from_secs(10))
        .timeout(Duration::from_secs(45))
        .pool_max_idle_per_host(8)
        .build()
}
