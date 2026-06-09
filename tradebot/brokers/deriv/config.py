"""
Deriv trading constants and configuration.
Reads from tradebot.config.settings — NO hardcoded secrets.
"""

from tradebot.config import settings

# ── WebSocket Endpoints ──
WS_LEGACY = "wss://ws.binaryws.com/websockets/v3"
WS_NEW_DEMO = "wss://api.derivws.com/trading/v1/options/ws/demo"
WS_NEW_REAL = "wss://api.derivws.com/trading/v1/options/ws/real"
REST_BASE = "https://api.derivws.com"
REST_OTP = f"{REST_BASE}/trading/v1/options/accounts/{{account_id}}/otp"
REST_ACCOUNTS = f"{REST_BASE}/trading/v1/options/accounts"

# ── Connection Tuning (from settings) ──
DEFAULT_APP_ID = settings.DERIV_APP_ID or "33uQ6fU4eIRvJc6jkYeEa"
PING_INTERVAL = settings.WS_PING_INTERVAL
PING_TIMEOUT = settings.WS_PING_TIMEOUT
WS_TIMEOUT = settings.WS_TIMEOUT
MAX_SIZE = settings.WS_MAX_SIZE

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

# ── Default Trading Params (from settings) ──
DEFAULT_SYMBOL = settings.DERIV_SYMBOL
DEFAULT_STAKE = settings.DERIV_INITIAL_STAKE
DEFAULT_CONTRACT_TYPE = settings.DERIV_CONTRACT_TYPE
DEFAULT_BARRIER = settings.DERIV_BARRIER
DEFAULT_DURATION = settings.DERIV_DURATION
DEFAULT_CURRENCY = "USD"
DEFAULT_BASIS = "stake"

# ── Risk Limits (from settings) ──
CONFIG_L_SL = settings.CONFIG_L_SL_POINTS
CONFIG_L_TP = settings.CONFIG_L_TP_POINTS
CONFIG_L_RR = settings.CONFIG_L_RR
DAILY_TP = settings.DAILY_TAKE_PROFIT
DAILY_SL = settings.DAILY_STOP_LOSS

INITIAL_STAKE = settings.DERIV_INITIAL_STAKE
STAKE_MULTIPLIER = settings.DERIV_STAKE_MULTIPLIER
MAX_OPS = settings.DERIV_MAX_OPS
MAX_STAKE_MULTIPLIER = settings.DERIV_MAX_STAKE_MULTIPLIER

# ── Pattern Analysis ──
TICK_HISTORY = settings.DERIV_TICK_HISTORY
MAX_PATTERN_LOOKBACK = 100
ANTI_FLOOD_WINDOW = settings.ANTI_FLOOD_WINDOW
ANTI_FLOOD_MAX = settings.ANTI_FLOOD_MAX

# ── Actuary Config ──
TARGET_CARRIERS = [int(x.strip()) for x in settings.TARGET_CARRIERS.split(",")]
MAX_JARING_TICKS = settings.MAX_JARING_TICKS
MIN_MOMEN1 = settings.MIN_MOMEN1
MIN_MOMEN2 = settings.MIN_MOMEN2
MIN_CONFIDENCE = settings.DERIV_MIN_CONFIDENCE
MAX_SHOTS = settings.MAX_SHOTS
DEFAULT_MIN_THRESHOLD = settings.DEFAULT_MIN_THRESHOLD

# ── Actuary v5 Cognitive ──
PATTERN_BLACKLIST_HOURS = settings.PATTERN_BLACKLIST_HOURS
LATENCY_TRAP_MS = settings.LATENCY_TRAP_MS
LATENCY_TRAP_LIMIT = settings.LATENCY_TRAP_LIMIT
MARKET_WIN_COOLDOWN_MIN = settings.MARKET_WIN_COOLDOWN_MIN
MARKET_LOSS_BLACKLIST_MIN = settings.MARKET_LOSS_BLACKLIST_MIN
LOCK_TP_HOURS = settings.LOCK_TP_HOURS
LOCK_SL_HOURS = settings.LOCK_SL_HOURS
PAYOUT_MULTIPLIER = settings.PAYOUT_MULTIPLIER
