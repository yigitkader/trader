//! CLOB emirleri için ortam değişkenleri. İsimler ileride resmi Polymarket client ile hizalanabilir.

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ExecutionMode {
    /// Sadece konsola plan yaz; ağa emir gitmez.
    DryRun,
    /// Gerçek emir (dispatch implement edilene kadar `bail!`).
    Live,
}

fn env_trim(key: &str) -> Option<String> {
    std::env::var(key)
        .ok()
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
}

impl ExecutionMode {
    fn from_env(raw: &str) -> Self {
        match raw.to_ascii_lowercase().as_str() {
            "live" | "1" | "true" | "yes" => ExecutionMode::Live,
            _ => ExecutionMode::DryRun,
        }
    }
}

#[derive(Debug, Clone)]
pub struct ExecutionConfig {
    pub mode: ExecutionMode,
    pub clob_base: String,
    pub api_key: Option<String>,
    pub api_secret: Option<String>,
    pub api_passphrase: Option<String>,
    /// EIP-712 / cüzdan imzası için (hangi akışta gerekiyorsa sonra bağlanır).
    pub private_key: Option<String>,
    l2_credentials_complete: bool,
}

/// `true` yapılınca `live_orders_enabled()` CLOB’a gerçekten POST deneyebilir (henüz false).
const CLOB_ORDER_DISPATCH_IMPLEMENTED: bool = false;

impl ExecutionConfig {
    /// `.env` + process env. Dosya yoksa sessizce atlanır.
    pub fn load() -> Self {
        let _ = dotenvy::dotenv();

        let mode_raw = env_trim("POLYMARKET_EXECUTION_MODE").unwrap_or_else(|| "dry_run".into());
        let mode = ExecutionMode::from_env(&mode_raw);

        let clob_base = env_trim("POLYMARKET_CLOB_BASE")
            .unwrap_or_else(|| "https://clob.polymarket.com".into());

        let api_key = env_trim("POLYMARKET_API_KEY");
        let api_secret = env_trim("POLYMARKET_API_SECRET");
        let api_passphrase = env_trim("POLYMARKET_API_PASSPHRASE");
        let private_key = env_trim("POLYMARKET_PRIVATE_KEY");

        let l2_credentials_complete =
            api_key.is_some() && api_secret.is_some() && api_passphrase.is_some();

        if matches!(mode, ExecutionMode::Live) && !l2_credentials_complete {
            eprintln!(
                "Uyarı: POLYMARKET_EXECUTION_MODE=live ama L2 anahtar seti eksik \
                 (POLYMARKET_API_KEY / _SECRET / _PASSPHRASE). Emir gönderilmeyecek."
            );
        }

        Self {
            mode,
            clob_base,
            api_key,
            api_secret,
            api_passphrase,
            private_key,
            l2_credentials_complete,
        }
    }

    /// Gerçek HTTP emri denenebilir mi? (Dispatch yazılınca burası kullanılacak.)
    pub fn live_orders_enabled(&self) -> bool {
        CLOB_ORDER_DISPATCH_IMPLEMENTED
            && matches!(self.mode, ExecutionMode::Live)
            && self.l2_credentials_complete
    }

    pub fn l2_credentials_ready(&self) -> bool {
        self.l2_credentials_complete
    }

    pub fn describe(&self) -> String {
        format!(
            "mode={:?} clob_base={} l2_creds={} signing_key={} dispatch_impl={}",
            self.mode,
            self.clob_base,
            if self.l2_credentials_complete { "ok" } else { "eksik" },
            if self.private_key.is_some() {
                "var (şimdilik kullanılmıyor)"
            } else {
                "yok (normal — imza katmanı sonra)"
            },
            CLOB_ORDER_DISPATCH_IMPLEMENTED
        )
    }
}
