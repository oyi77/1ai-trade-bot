"""
Whitelabel Configuration — feature-gated market access per instance.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Each whitelabel instance can toggle which markets are active:
- ALL: everything enabled
- FOREX: forex pairs (EUR/USD, GBP/USD, etc.)
- STOCKITY: Stockity binary options (CRYPTO_IDX, BTC_IDX etc.)
- DERIV: Deriv synthetic indices (R_75, 1HZ10V etc.)
- CRYPTO: Crypto spot/futures (BTC-USD, ETH-USD etc.)

Commands and features respect the active flags.
"""

from agent.database import (
    FEATURE_ALL, FEATURE_FOREX, FEATURE_STOCKITY, FEATURE_DERIV, FEATURE_CRYPTO,
    ALL_FEATURES, parse_features, create_whitelabel, get_whitelabel,
    get_all_whitelabels, update_whitelabel_features, set_whitelabel_active,
    is_feature_active,
)

DEFAULT_WHITELABEL = "default"


def get_default_features() -> set[str]:
    return ALL_FEATURES


def market_to_feature(market: str) -> str:
    """Map a market/symbol/command to its feature flag."""
    m = market.upper()
    if m in ("CRYPTO_IDX", "BTC_IDX", "ETH_IDX", "GOLD_IDX"):
        return FEATURE_STOCKITY
    if m.startswith("R_") or m.startswith("1HZ") or m in ("BOOM", "CRASH", "STP", "STABLE", "VOLATILE"):
        return FEATURE_DERIV
    if m in ("BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "BNB-USD") or m.endswith("USDT"):
        return FEATURE_CRYPTO
    if m in ("EURUSD", "GBPUSD", "USDJPY", "EURUSD=X", "GBPUSD=X", "USDJPY=X"):
        return FEATURE_FOREX
    return FEATURE_ALL


def is_market_allowed(name: str, market: str, features: set[str] | None = None) -> bool:
    """Check if a market is allowed for a whitelabel."""
    if FEATURE_ALL in (features or get_default_features()):
        return True
    needed = market_to_feature(market)
    return needed in (features or get_default_features())
