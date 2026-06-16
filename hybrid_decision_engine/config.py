"""
Hybrid Decision Engine — Configuration
=======================================
All timeouts, thresholds, paths, and tunables in one place.
"""
import os
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────
PROJECT_DIR = Path(__file__).resolve().parent.parent
ENGINE_DIR = Path(__file__).resolve().parent
DATA_DIR = ENGINE_DIR / "data"
OHLCV_DIR = DATA_DIR / "ohlcv"
SIGNALS_DIR = DATA_DIR / "signals"
DECISIONS_DIR = DATA_DIR / "decisions"
LOG_DIR = DATA_DIR / "logs"

for d in [OHLCV_DIR, SIGNALS_DIR, DECISIONS_DIR, LOG_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ── Server ─────────────────────────────────────────────────────────
HOST = os.environ.get("HYBRID_HOST", "127.0.0.1")
PORT = int(os.environ.get("HYBRID_PORT", "8770"))

# ── MT5 Bridge (existing service) ──────────────────────────────────
BRIDGE_HOST = os.environ.get("BRIDGE_HOST", "127.0.0.1")
BRIDGE_PORT = int(os.environ.get("BRIDGE_PORT", "8765"))
BRIDGE_URL = f"http://{BRIDGE_HOST}:{BRIDGE_PORT}"
BRIDGE_API_KEY = os.environ.get("BRIDGE_API_KEY", "")

# ── Timeouts (seconds) ────────────────────────────────────────────
TIMEOUT_DATA_FETCH = 10          # Max time to fetch OHLCV data
TIMEOUT_LSTM = 30                # Max time for LSTM analysis
TIMEOUT_ZF = 15                  # Max time for ZF-Core analysis
TIMEOUT_INTEGRITY = 10           # Max time for Market Integrity
TIMEOUT_TOTAL_PIPELINE = 60      # Max total analysis pipeline

# ── Cache TTL ──────────────────────────────────────────────────────
OHLCV_CACHE_TTL = 60             # Seconds before stale data refresh
PRICE_CACHE_TTL = 5              # Seconds for live price cache

# ── ZF-Core Thresholds (68/32 system) ─────────────────────────────
ZF_UPPER_THRESHOLD = 0.68        # 1 std dev above mean → overbought zone
ZF_LOWER_THRESHOLD = 0.32        # 1 std dev below mean → oversold zone
ZF_EXTREME_UPPER = 0.90          # Extreme overbought → high confidence
ZF_EXTREME_LOWER = 0.10          # Extreme oversold → high confidence
ZF_LOOKBACK = 20                 # Bars for Z-Score calculation
ZF_VOLUME_LOOKBACK = 20          # Bars for volume Z-Score

# ── Signal Quality ─────────────────────────────────────────────────
MIN_CONFIDENCE_SOLO = 0.85       # Minimum confidence for solo analyzer signal
MIN_CONFIDENCE_DUAL = 0.60       # Minimum confidence for dual consensus
SOLO_CONFIDENCE_BOOST = 1.2      # Multiplier when dual analyzers agree
MAX_DAILY_SIGNALS = 10           # Circuit breaker: max signals per day
SIGNAL_COOLDOWN = 300            # Seconds between same-pair signals

# ── Circuit Breaker ────────────────────────────────────────────────
CB_FAILURE_THRESHOLD = 3         # Failures before pause
CB_PAUSE_DURATION = 600          # 10 minutes pause after circuit break

# ── Data Sources ───────────────────────────────────────────────────
DEFAULT_SYMBOLS = ["XAUUSD", "BTCUSD", "ETHUSD", "USOIL"]
DEFAULT_TIMEFRAMES = ["M1", "M5", "M15", "H1", "H4", "D1"]
CCXT_DEFAULT_LIMIT = 200         # Default candles from ccxt
YFINANCE_INTERVAL_MAP = {
    "M1": "1m", "M5": "5m", "M15": "15m",
    "H1": "1h", "H4": "4h", "D1": "1d",
}
YFINANCE_MAX_PERIOD = "60d"      # yfinance limit for intraday

# ── Logging ────────────────────────────────────────────────────────
LOG_LEVEL = os.environ.get("HYBRID_LOG_LEVEL", "INFO")
LOG_FORMAT = "%(asctime)s [%(name)s] %(levelname)s %(message)s"
