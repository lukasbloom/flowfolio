"""Provider registry: the single source of truth for the 5 API-key providers.

Pure static data: id, the user_setting key name, display label, "what this key
enables" blurb, free-tier text, register URL, and whether the key is optional.
The wizard and Settings render straight off this list, and key_store derives
KEY_STORE_KEYS + get_key_status from it so the two never drift.

The order is LOAD-BEARING: Finnhub -> CoinGecko -> Alpha Vantage ->
Twelve Data -> GitHub. Do NOT import the pricing-client modules here — the
registry stays a pure-data leaf to avoid an import cycle (key_test.py owns the
test-call dispatch keyed by provider id).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Provider:
    """One key-bearing provider rendered by the wizard / Settings."""

    id: str  # route slug, ^[a-z_]+$
    setting_key: str  # the user_setting row name (legacy names kept for continuity)
    label: str
    blurb: str  # what configuring this key enables
    free_tier: str  # free-tier limit text
    register_url: str
    optional: bool = False


PROVIDERS: tuple[Provider, ...] = (
    Provider(
        id="finnhub",
        setting_key="finnhub_api_key",
        label="Finnhub",
        blurb="Primary live price source for stocks and ETFs.",
        free_tier="60 calls/minute",
        register_url="https://finnhub.io/register",
    ),
    Provider(
        id="coingecko",
        setting_key="coingecko_api_key",
        label="CoinGecko",
        blurb="Primary live price source for crypto and stablecoins.",
        free_tier="~30 calls/minute, 10,000/month",
        register_url="https://www.coingecko.com/en/developers/dashboard",
    ),
    Provider(
        id="alpha_vantage",
        setting_key="alpha_vantage_api_key",
        label="Alpha Vantage",
        blurb="Fallback stock price source when Finnhub does not cover a ticker.",
        free_tier="25 calls/day, 5/minute",
        register_url="https://www.alphavantage.co/support/#api-key",
    ),
    Provider(
        id="twelve_data",
        setting_key="twelve_data_api_key",
        label="Twelve Data",
        blurb="Secondary stock price fallback in the pricing chain.",
        free_tier="800 calls/day",
        register_url="https://twelvedata.com/pricing",
    ),
    Provider(
        id="github",
        setting_key="github_token",
        label="GitHub",
        blurb="Raises the self-update release-check rate limit. Optional.",
        free_tier="60/hour unauthenticated, 5,000/hour with a token",
        register_url="https://github.com/settings/tokens",
        optional=True,
    ),
)


def get_provider(provider_id: str) -> Provider | None:
    """Look up a provider by its route slug, or None on a miss."""
    for provider in PROVIDERS:
        if provider.id == provider_id:
            return provider
    return None
