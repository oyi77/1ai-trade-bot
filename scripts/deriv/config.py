"""Deriv trading constants and configuration."""
import os

WS_LEGACY = "wss://ws.derivws.com/websockets/v3"  # Updated from binaryws.com (deprecated)
WS_NEW_DEMO = "wss://api.derivws.com/trading/v1/options/ws/demo"
WS_NEW_REAL = "wss://api.derivws.com/trading/v1/options/ws/real"
REST_BASE = "https://api.derivws.com"
REST_OTP = f"{REST_BASE}/trading/v1/options/accounts/{{account_id}}/otp"
REST_ACCOUNTS = f"{REST_BASE}/trading/v1/options/accounts"

DEFAULT_APP_ID = os.environ.get("DERIV_APP_ID", "")  # Must be set in .env

# ── Connection Tuning ──
PING_INTERVAL = 20
PING_TIMEOUT = 10
WS_TIMEOUT = 15
MAX_SIZE = 2 ** 20

# ── Market Symbols ──
SYNTHETIC_INDICES = [
    "R_10", "R_25", "R_50", "R_75", "R_100",
    "1HZ10V", "1HZ25V", "1HZ50V", "1HZ75V", "1HZ100V",
    "BOOM300", "BOOM500", "CRASH300", "CRASH500",
    "JD10", "JD25", "JD50", "JD75", "JD100",
]

SYMBOL_LABELS = {
    "R_10": "Vol 10 (1s)", "R_25": "Vol 25", "R_50": "Vol 50",
    "R_75": "Vol 75", "R_100": "Vol 100",
    "1HZ10V": "1Hz Vol 10", "1HZ25V": "1Hz Vol 25",
    "1HZ50V": "1Hz Vol 50", "1HZ75V": "1Hz Vol 75",
    "1HZ100V": "1Hz Vol 100",
    "BOOM300": "Boom 300", "BOOM500": "Boom 500",
    "CRASH300": "Crash 300", "CRASH500": "Crash 500",
}

# ── Contract Types ──
CONTRACT_TYPES = {
    "DIGITMATCH": "Last digit == barrier",
    "DIGITOVER": "Last digit > barrier",
    "DIGITUNDER": "Last digit < barrier",
    "DIGITODD": "Last digit odd",
    "DIGITEVEN": "Last digit even",
    "RISE": "Price goes up",
    "FALL": "Price goes down",
}

# ── Default Trading Params ──
DEFAULT_SYMBOL = "R_75"
DEFAULT_STAKE = 1.0
DEFAULT_CONTRACT_TYPE = "DIGITMATCH"
DEFAULT_BARRIER = 7
DEFAULT_DURATION = 1   # ticks
DEFAULT_CURRENCY = "USD"
DEFAULT_BASIS = "stake"

# ── Config L Risk (from backtest, 8 Jun 2026) ──
CONFIG_L_SL = 32.0     # point loss limit
CONFIG_L_TP = 52.0     # point profit target
CONFIG_L_RR = 1.625    # risk:reward
# In USD terms:
DAILY_TP = 5.0         # take profit ($)
DAILY_SL = -8.0        # stop loss ($)

INITIAL_STAKE = 0.35
STAKE_MULTIPLIER = 1.55
MAX_OPS = 3
MAX_STAKE_MULTIPLIER = 10

# ── Pattern Analysis ──
TICK_HISTORY = 100
MAX_PATTERN_LOOKBACK = 100
ANTI_FLOOD_WINDOW = 20
ANTI_FLOOD_MAX = 3

# ── Actuary Config ──
TARGET_CARRIERS = [1, 2, 3, 4]
MAX_JARING_TICKS = 3
MIN_MOMEN1 = 1
MIN_MOMEN2 = 1
MIN_CONFIDENCE = 0.3
MAX_SHOTS = 2
DEFAULT_MIN_THRESHOLD = 3

# ── Actuary v5 Cognitive ──
PATTERN_BLACKLIST_HOURS = 24
LATENCY_TRAP_MS = 350
LATENCY_TRAP_LIMIT = 2
MARKET_WIN_COOLDOWN_MIN = 5
MARKET_LOSS_BLACKLIST_MIN = 60
LOCK_TP_HOURS = 12
LOCK_SL_HOURS = 2
PAYOUT_MULTIPLIER = 8.33
