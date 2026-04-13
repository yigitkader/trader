//! Polymarket CLOB limit emirleri (`polymarket-client-sdk`).

use std::str::FromStr;

use alloy::signers::Signer as _;
use anyhow::Context;
use polymarket_client_sdk::AMOY;
use polymarket_client_sdk::POLYGON;
use polymarket_client_sdk::auth::state::Authenticated;
use alloy::signers::local::PrivateKeySigner;
use polymarket_client_sdk::auth::{Credentials, Normal};
use polymarket_client_sdk::clob::types::{OrderType, Side, SignatureType};
use polymarket_client_sdk::clob::{Client, Config};
use polymarket_client_sdk::types::{Address, ChainId, Decimal, U256};
use uuid::Uuid;

use super::config::ExecutionConfig;
use super::dispatch::{limit_price_for_buy, OrderPlan};
use crate::types::Market;

type AuthClobClient = Client<Authenticated<Normal>>;

fn sdk_chain(chain_id: u64) -> anyhow::Result<ChainId> {
    match chain_id {
        137 => Ok(POLYGON),
        80002 => Ok(AMOY),
        _ => anyhow::bail!(
            "POLYMARKET_CHAIN_ID={} desteklenmiyor; 137 (Polygon) veya 80002 (Amoy) kullanın",
            chain_id
        ),
    }
}

fn signature_type_from_cfg(code: u8) -> anyhow::Result<SignatureType> {
    match code {
        0 => Ok(SignatureType::Eoa),
        1 => Ok(SignatureType::Proxy),
        2 => Ok(SignatureType::GnosisSafe),
        _ => anyhow::bail!(
            "POLYMARKET_SIGNATURE_TYPE={} geçersiz; 0=EOA, 1=Proxy, 2=GnosisSafe",
            code
        ),
    }
}

fn f32_to_decimal(p: f32) -> anyhow::Result<Decimal> {
    if !p.is_finite() {
        anyhow::bail!("fiyat sonlu değil");
    }
    format!("{:.6}", p)
        .parse()
        .map_err(|e| anyhow::anyhow!("Decimal parse: {e}"))
}

fn build_signer(cfg: &ExecutionConfig) -> anyhow::Result<PrivateKeySigner> {
    let pk = cfg
        .private_key
        .as_ref()
        .ok_or_else(|| anyhow::anyhow!("POLYMARKET_PRIVATE_KEY eksik"))?;
    let chain = sdk_chain(cfg.chain_id)?;
    PrivateKeySigner::from_str(pk.trim())
        .context("private key parse (0x… hex beklenir)")
        .map(|s| s.with_chain_id(Some(chain)))
}

fn build_credentials(cfg: &ExecutionConfig) -> anyhow::Result<Credentials> {
    let key = Uuid::parse_str(
        cfg.api_key
            .as_deref()
            .ok_or_else(|| anyhow::anyhow!("POLYMARKET_API_KEY eksik"))?,
    )
    .context("POLYMARKET_API_KEY UUID formatında olmalı")?;
    let secret = cfg
        .api_secret
        .clone()
        .ok_or_else(|| anyhow::anyhow!("POLYMARKET_API_SECRET eksik"))?;
    let passphrase = cfg
        .api_passphrase
        .clone()
        .ok_or_else(|| anyhow::anyhow!("POLYMARKET_API_PASSPHRASE eksik"))?;
    Ok(Credentials::new(key, secret, passphrase))
}

async fn authenticate_clob(
    cfg: &ExecutionConfig,
    signer: &PrivateKeySigner,
    creds: Credentials,
) -> anyhow::Result<AuthClobClient> {
    let host = cfg.clob_base.trim_end_matches('/');
    let client = Client::new(host, Config::default()).map_err(|e| anyhow::anyhow!("{e}"))?;

    let sig = signature_type_from_cfg(cfg.signature_type)?;

    let authed = match (sig, cfg.funder_address.as_deref()) {
        (SignatureType::Eoa, _) => client
            .authentication_builder(signer)
            .credentials(creds)
            .signature_type(SignatureType::Eoa)
            .authenticate()
            .await
            .map_err(|e| anyhow::anyhow!("{e}"))?,
        (SignatureType::Proxy, Some(addr)) => {
            let funder: Address = addr
                .parse()
                .map_err(|e| anyhow::anyhow!("POLYMARKET_FUNDER_ADDRESS: {e}"))?;
            client
                .authentication_builder(signer)
                .credentials(creds)
                .signature_type(SignatureType::Proxy)
                .funder(funder)
                .authenticate()
                .await
                .map_err(|e| anyhow::anyhow!("{e}"))?
        }
        (SignatureType::Proxy, None) => client
            .authentication_builder(signer)
            .credentials(creds)
            .signature_type(SignatureType::Proxy)
            .authenticate()
            .await
            .map_err(|e| anyhow::anyhow!("{e}"))?,
        (SignatureType::GnosisSafe, Some(addr)) => {
            let funder: Address = addr
                .parse()
                .map_err(|e| anyhow::anyhow!("POLYMARKET_FUNDER_ADDRESS: {e}"))?;
            client
                .authentication_builder(signer)
                .credentials(creds)
                .signature_type(SignatureType::GnosisSafe)
                .funder(funder)
                .authenticate()
                .await
                .map_err(|e| anyhow::anyhow!("{e}"))?
        }
        (SignatureType::GnosisSafe, None) => {
            anyhow::bail!("GnosisSafe için POLYMARKET_FUNDER_ADDRESS gerekli");
        }
        (other, _) => anyhow::bail!("Desteklenmeyen signature_type: {:?}", other),
    };

    Ok(authed)
}

/// Limit alım (GTC) — outcome token için `Side::Buy`.
pub async fn post_limit_buy(cfg: &ExecutionConfig, market: &Market, plan: &OrderPlan) -> anyhow::Result<String> {
    let token_str = plan
        .token_id
        .as_deref()
        .ok_or_else(|| anyhow::anyhow!("token_id yok (Gamma token alanları boş olabilir)"))?;
    let token_id = U256::from_str(token_str).context("token_id U256 parse")?;

    let limit = limit_price_for_buy(market, &plan.decision, cfg.price_slippage);
    let price = f32_to_decimal(limit)?;
    let size = cfg.order_size;

    let signer = build_signer(cfg)?;
    let creds = build_credentials(cfg)?;
    let client = authenticate_clob(cfg, &signer, creds).await?;

    let signable = client
        .limit_order()
        .token_id(token_id)
        .side(Side::Buy)
        .price(price)
        .size(size)
        .order_type(OrderType::GTC)
        .build()
        .await
        .map_err(|e| anyhow::anyhow!("{e}"))?;

    let signed = client
        .sign(&signer, signable)
        .await
        .map_err(|e| anyhow::anyhow!("{e}"))?;

    let resp = client
        .post_order(signed)
        .await
        .map_err(|e| anyhow::anyhow!("{e}"))?;

    if !resp.success {
        anyhow::bail!(
            "CLOB reddetti: {:?} — {}",
            resp.status,
            resp.error_msg.unwrap_or_default()
        );
    }

    Ok(resp.order_id)
}
