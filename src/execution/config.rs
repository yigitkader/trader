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

/// Limit emrin agresifligi: maker = spread icine, taker = daha hizli dolum (daha yuksek maliyet).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum OrderStyle {
    Maker,
    Taker,
}

impl OrderStyle {
    fn from_env(raw: &str) -> Self {
        match raw.to_ascii_lowercase().as_str() {
            "taker" | "aggressive" | "1" => OrderStyle::Taker,
            _ => OrderStyle::Maker,
        }
    }
}

#[derive(Clone)]
pub struct ExecutionConfig {
    pub mode: ExecutionMode,
    pub clob_base: String,
    pub api_key: Option<String>,
    pub api_secret: Option<String>,
    pub api_passphrase: Option<String>,
    /// EIP-712 imzası için (canlı emirde zorunlu).
    pub private_key: Option<String>,
    /// Açık onay: `1`/`true` olmadan gerçek POST yapılmaz.
    pub live_trading: bool,
    /// 137 Polygon, 80002 Amoy.
    pub chain_id: u64,
    /// 0 EOA, 1 Proxy, 2 GnosisSafe — Polymarket hesap türüne göre.
    pub signature_type: u8,
    pub funder_address: Option<String>,
    /// Limit emir büyüklüğü (pay / lot; tick kurallarına tabi).
    pub order_size: rust_decimal::Decimal,
    /// Referans fiyata eklenecek tavan (ör. 0.02 → mid+2¢ limit).
    pub price_slippage: f32,
    /// Aynı pazara tekrar emir atılmadan önce beklenen süre (saniye).
    pub cooldown_per_market_secs: u64,
    /// Bir tick'te atılabilecek maksimum emir sayısı.
    pub max_orders_per_tick: u16,
    /// Aynı anda açık olabilecek farklı pazar sayısı.
    pub max_open_markets: u16,
    /// Günlük toplam emir tutarı (nominal, USDC cinsinden).
    pub max_daily_notional: f64,
    pub order_style: OrderStyle,
    /// Kelly payının bu kadarı uygulanır (0.5 = Half-Kelly).
    pub kelly_fraction: f32,
    /// Bu efektif Kelly’de ~1x baz lot (`kelly_fraction` sonrası).
    pub kelly_target: f32,
    pub order_size_min: rust_decimal::Decimal,
    pub order_size_max: rust_decimal::Decimal,
    /// Maker modunda L2 yoksa veya spread çok dar ise emir gönderme.
    pub maker_strict: bool,
    /// Doluysa lot, kasanın `bankroll_min_frac`–`bankroll_max_frac` arası USDC payına göre.
    pub bankroll_usdc: Option<f64>,
    pub bankroll_max_frac: f32,
    pub bankroll_min_frac: f32,
    l2_credentials_complete: bool,
}

impl std::fmt::Debug for ExecutionConfig {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("ExecutionConfig")
            .field("mode", &self.mode)
            .field("clob_base", &self.clob_base)
            .field("api_key", &self.api_key.as_ref().map(|_| "***"))
            .field("api_secret", &self.api_secret.as_ref().map(|_| "***"))
            .field("api_passphrase", &self.api_passphrase.as_ref().map(|_| "***"))
            .field("private_key", &self.private_key.as_ref().map(|_| "***"))
            .field("live_trading", &self.live_trading)
            .field("chain_id", &self.chain_id)
            .field("signature_type", &self.signature_type)
            .field("order_size", &self.order_size)
            .field("price_slippage", &self.price_slippage)
            .field("cooldown_per_market_secs", &self.cooldown_per_market_secs)
            .field("max_orders_per_tick", &self.max_orders_per_tick)
            .field("max_open_markets", &self.max_open_markets)
            .field("max_daily_notional", &self.max_daily_notional)
            .field("order_style", &self.order_style)
            .field("kelly_fraction", &self.kelly_fraction)
            .field("kelly_target", &self.kelly_target)
            .field("order_size_min", &self.order_size_min)
            .field("order_size_max", &self.order_size_max)
            .field("maker_strict", &self.maker_strict)
            .field("bankroll_usdc", &self.bankroll_usdc)
            .field("bankroll_max_frac", &self.bankroll_max_frac)
            .field("bankroll_min_frac", &self.bankroll_min_frac)
            .finish()
    }
}

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

        let live_trading = env_trim("POLYMARKET_LIVE_TRADING")
            .map(|s| matches!(s.to_ascii_lowercase().as_str(), "1" | "true" | "yes"))
            .unwrap_or(false);

        let chain_id = env_trim("POLYMARKET_CHAIN_ID")
            .and_then(|s| s.parse().ok())
            .unwrap_or(137u64);

        let signature_type = env_trim("POLYMARKET_SIGNATURE_TYPE")
            .and_then(|s| s.parse().ok())
            .unwrap_or(0u8);

        let funder_address = env_trim("POLYMARKET_FUNDER_ADDRESS");

        let order_size = env_trim("POLYMARKET_ORDER_SIZE")
            .and_then(|s| s.parse().ok())
            .unwrap_or_else(|| "5".parse().expect("literal decimal"));

        let price_slippage = env_trim("POLYMARKET_PRICE_SLIPPAGE")
            .and_then(|s| s.parse().ok())
            .unwrap_or(0.02f32);

        let cooldown_per_market_secs = env_trim("POLYMARKET_COOLDOWN_SECS")
            .and_then(|s| s.parse().ok())
            .unwrap_or(120u64);

        let max_orders_per_tick = env_trim("POLYMARKET_MAX_ORDERS_PER_TICK")
            .and_then(|s| s.parse().ok())
            .unwrap_or(2u16);

        let max_open_markets = env_trim("POLYMARKET_MAX_OPEN_MARKETS")
            .and_then(|s| s.parse().ok())
            .unwrap_or(5u16);

        let max_daily_notional = env_trim("POLYMARKET_MAX_DAILY_NOTIONAL")
            .and_then(|s| s.parse().ok())
            .unwrap_or(100.0f64);

        let order_style = env_trim("POLYMARKET_ORDER_STYLE")
            .map(|s| OrderStyle::from_env(&s))
            .unwrap_or(OrderStyle::Maker);

        let kelly_fraction = env_trim("POLYMARKET_KELLY_FRACTION")
            .and_then(|s| s.parse::<f32>().ok())
            .filter(|v| v.is_finite())
            .unwrap_or(0.5f32);

        let kelly_target = env_trim("POLYMARKET_KELLY_TARGET")
            .and_then(|s| s.parse::<f32>().ok())
            .filter(|v| v.is_finite())
            .unwrap_or(0.08f32);

        let order_size_min = env_trim("POLYMARKET_ORDER_SIZE_MIN")
            .and_then(|s| s.parse().ok())
            .unwrap_or_else(|| "1".parse().expect("literal decimal"));

        let order_size_max = env_trim("POLYMARKET_ORDER_SIZE_MAX")
            .and_then(|s| s.parse().ok())
            .unwrap_or_else(|| "50".parse().expect("literal decimal"));

        let maker_strict = env_trim("POLYMARKET_MAKER_STRICT")
            .map(|s| matches!(s.to_ascii_lowercase().as_str(), "1" | "true" | "yes"))
            .unwrap_or(false);

        let bankroll_usdc = env_trim("POLYMARKET_BANKROLL_USDC").and_then(|s| s.parse::<f64>().ok());

        let bankroll_max_frac = env_trim("POLYMARKET_BANKROLL_MAX_FRAC")
            .and_then(|s| s.parse::<f32>().ok())
            .filter(|v| v.is_finite())
            .unwrap_or(0.10f32);

        let bankroll_min_frac = env_trim("POLYMARKET_BANKROLL_MIN_FRAC")
            .and_then(|s| s.parse::<f32>().ok())
            .filter(|v| v.is_finite())
            .unwrap_or(0.01f32);

        let l2_credentials_complete =
            api_key.is_some() && api_secret.is_some() && api_passphrase.is_some();

        if matches!(mode, ExecutionMode::Live) && !l2_credentials_complete {
            eprintln!(
                "Uyarı: POLYMARKET_EXECUTION_MODE=live ama L2 anahtar seti eksik \
                 (POLYMARKET_API_KEY / _SECRET / _PASSPHRASE). Emir gönderilmeyecek."
            );
        }

        if live_trading && private_key.is_none() {
            eprintln!(
                "Uyarı: POLYMARKET_LIVE_TRADING açık ama POLYMARKET_PRIVATE_KEY yok — canlı emir atılamaz."
            );
        }

        Self {
            mode,
            clob_base,
            api_key,
            api_secret,
            api_passphrase,
            private_key,
            live_trading,
            chain_id,
            signature_type,
            funder_address,
            order_size,
            price_slippage,
            cooldown_per_market_secs,
            max_orders_per_tick,
            max_open_markets,
            max_daily_notional,
            order_style,
            kelly_fraction,
            kelly_target,
            order_size_min,
            order_size_max,
            maker_strict,
            bankroll_usdc,
            bankroll_max_frac,
            bankroll_min_frac,
            l2_credentials_complete,
        }
    }

    /// Gerçek CLOB `POST /order` çağrısı yapılabilir mi?
    pub fn live_orders_enabled(&self) -> bool {
        self.live_trading
            && matches!(self.mode, ExecutionMode::Live)
            && self.l2_credentials_complete
            && self.private_key.is_some()
    }

    pub fn l2_credentials_ready(&self) -> bool {
        self.l2_credentials_complete
    }

    pub fn describe(&self) -> String {
        format!(
            "mode={:?} clob_base={} l2_creds={} signing_key={} live_trading={} chain_id={} sig_type={}",
            self.mode,
            self.clob_base,
            if self.l2_credentials_complete { "ok" } else { "eksik" },
            if self.private_key.is_some() { "var" } else { "yok" },
            self.live_trading,
            self.chain_id,
            self.signature_type,
        )
    }
}
