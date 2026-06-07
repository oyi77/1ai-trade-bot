"""market_data/__init__.py — Delegates to scripts/market_data.py (authoritative implementation)."""
import sys, os, importlib.util

# Load the authoritative module from scripts/
_scripts_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts")
_spec = importlib.util.spec_from_file_location(
    "_market_data_impl",
    os.path.join(_scripts_dir, "market_data.py")
)
_impl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_impl)

# Re-export
UnifiedMarketData = _impl.UnifiedMarketData
Quote = _impl.Quote
OHLCVBar = _impl.OHLCVBar
get_market = _impl.get_market

# Backward compat alias
MarketQuote = Quote

__all__ = ["UnifiedMarketData", "Quote", "OHLCVBar", "get_market", "MarketQuote"]