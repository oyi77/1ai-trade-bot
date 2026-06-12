"""
Pydantic Settings — reads from .env and environment variables.
No hardcoded secrets. Everything configurable via environment.
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ═══════════════════════════════════════════════════════════════
    #  DERIV — Deriv Broker Credentials & Trading Parameters
    # ═══════════════════════════════════════════════════════════════

    # ── API Credentials ──
    DERIV_APP_ID: str = ""
    DERIV_PAT_TOKEN: str = ""
    DERIV_ACCOUNT_ID: str = ""
    DERIV_API_TOKEN: str = ""
    DERIV_MODE: str = "demo"  # "demo" | "real"

    # ── Symbol / Contract ──
    DERIV_SYMBOL: str = "R_75"
    DERIV_CONTRACT_TYPE: str = "DIGITMATCH"
    DERIV_BARRIER: int = 7

    # ── Stake / Money Management ──
    DERIV_INITIAL_STAKE: float = 0.35
    DERIV_STAKE_MULTIPLIER: float = 1.55
    DERIV_MAX_OPS: int = 3
    DERIV_MAX_STAKE_MULTIPLIER: float = 10.0

    # ── Duration ──
    DERIV_DURATION: int = 1
    DERIV_DURATION_UNIT: str = "t"  # "t" = ticks, "m" = minutes, "h" = hours

    # ── Tick / Confidence ──
    DERIV_TICK_HISTORY: int = 100
    DERIV_MIN_CONFIDENCE: float = 0.3

    # ═══════════════════════════════════════════════════════════════
    #  ADMIN — Bot Administration
    # ═══════════════════════════════════════════════════════════════

    # Comma-separated Telegram user IDs with admin access
    # Gets /set_share, /set_rate, /set_plan, /admin commands
    ADMIN_USER_IDS: str = ""

    # ═══════════════════════════════════════════════════════════════
    #  BROKER — Generic Broker Configuration
    # ═══════════════════════════════════════════════════════════════

    BROKER_DRY_RUN: bool = True  # Paper trade by default
    BROKER_MAX_POSITIONS: int = 1  # Maximum concurrent positions
    BROKER_DEFAULT_STAKE: float = 0.35  # Default stake per trade
    BROKER_RECONNECT_DELAY: int = 5  # Seconds between reconnection attempts
    BROKER_RECONNECT_MAX_RETRIES: int = 10  # Max reconnection retries before giving up
    # ═══════════════════════════════════════════════════════════════
    #  MT5 — MetaTrader 5 Specific Settings
    # ═══════════════════════════════════════════════════════════════

    MT5_LOGIN: str = ""
    MT5_PASSWORD: str = ""
    MT5_SERVER: str = ""
    MT5_PATH: str = ""  # Path to terminal.exe (optional)
    MT5_TIMEOUT: int = 30  # Connection timeout (seconds)
    MT5_MAGIC_NUMBER: int = 101001  # EA magic number
    MT5_SYMBOLS: str = "XAUUSD"  # Comma-separated list of symbols
    MT5_ENABLE_NEWS_TRADING: bool = False
    MT5_MAX_SPREAD: float = 50.0  # Max spread in points
    MT5_SLIPPAGE: int = 10  # Max slippage in points

    # ═══════════════════════════════════════════════════════════════
    #  ENGINE — Signal Analysis Engines
    # ═══════════════════════════════════════════════════════════════

    ENGINE_CONSENSUS_MIN_VOTES: int = 2  # Minimum engines required for consensus
    ENGINE_CONSENSUS_WEIGHTED: bool = True  # Weight by historical accuracy
    ENGINE_CONFIDENCE_THRESHOLD: float = 0.5  # Minimum confidence to emit signal
    ENGINE_EXECUTION_TIMEOUT: int = 30  # Max seconds per engine execution
    ENGINE_CACHE_RESULTS: bool = True  # Cache engine outputs for repeated calls
    ENGINE_CACHE_TTL: int = 60  # Seconds to keep cached results

    # ═══════════════════════════════════════════════════════════════
    #  SIGNAL — Signal Pipeline Configuration
    # ═══════════════════════════════════════════════════════════════

    SIGNAL_MIN_CONFIDENCE: float = 0.3  # Minimum confidence to process signal
    SIGNAL_VALIDATION_STRICT: bool = True  # Reject signals missing required fields
    SIGNAL_DEDUP_WINDOW: int = 60  # Seconds to suppress duplicate signals
    SIGNAL_QUEUE_MAXSIZE: int = 100  # Maximum queued signals
    SIGNAL_PIPELINE_TIMEOUT: int = 10  # Seconds before pipeline stage times out
    SIGNAL_HISTORY_SIZE: int = 1000  # Max historical signals to retain

    # ═══════════════════════════════════════════════════════════════
    #  RISK — Risk Limits & Guardrails
    # ═══════════════════════════════════════════════════════════════

    DAILY_TAKE_PROFIT: float = 5.0
    DAILY_STOP_LOSS: float = -8.0
    CONFIG_L_SL_POINTS: float = 32.0
    CONFIG_L_TP_POINTS: float = 52.0
    CONFIG_L_RR: float = 1.625

    # ═══════════════════════════════════════════════════════════════
    #  PATTERN — Pattern Analysis & Actuary
    # ═══════════════════════════════════════════════════════════════

    TARGET_CARRIERS: str = "1,2,3,4"
    MAX_JARING_TICKS: int = 3
    MIN_MOMEN1: int = 1
    MIN_MOMEN2: int = 1
    ANTI_FLOOD_WINDOW: int = 20
    ANTI_FLOOD_MAX: int = 3
    DEFAULT_MIN_THRESHOLD: int = 3

    # ── Cognitive / Actuary ──
    PATTERN_BLACKLIST_HOURS: int = 24
    LATENCY_TRAP_MS: int = 350
    LATENCY_TRAP_LIMIT: int = 2
    MARKET_WIN_COOLDOWN_MIN: int = 5
    MARKET_LOSS_BLACKLIST_MIN: int = 60
    LOCK_TP_HOURS: int = 12
    LOCK_SL_HOURS: int = 2
    PAYOUT_MULTIPLIER: float = 8.33
    MAX_SHOTS: int = 2

    # ═══════════════════════════════════════════════════════════════
    #  MONITORING — Health Checks & Metrics
    # ═══════════════════════════════════════════════════════════════

    MONITORING_HEARTBEAT_INTERVAL: int = 60  # Seconds between heartbeat checks
    MONITORING_PROMETHEUS_ENABLED: bool = False  # Expose /metrics via Prometheus
    MONITORING_PROMETHEUS_PORT: int = 8000  # Prometheus HTTP server port
    MONITORING_MAX_POSITION_AGE_MIN: int = 120  # Alert if position open > 2h
    MONITORING_LATENCY_ALERT_MS: int = 500  # Alert on tick latency exceeding this
    MONITORING_PNL_DD_THRESHOLD: float = -20.0  # Alert on drawdown exceeding this
    MONITORING_HEALTH_LOG: bool = True  # Write periodic health snapshot to log

    # ═══════════════════════════════════════════════════════════════
    #  PAT — Generic PAT-Prefixed Configuration Vars
    # ═══════════════════════════════════════════════════════════════

    PAT_BLACKLIST_HOURS: int = 24
    PAT_WIN_COOLDOWN_MIN: int = 5
    PAT_LOSS_BLACKLIST_MIN: int = 60
    PAT_MAX_CONSECUTIVE_LOSSES: int = 3
    PAT_MIN_PROFIT_TARGET: float = 2.0
    PAT_MAX_DAILY_LOSS: float = -10.0
    PAT_RECOVERY_MODE: bool = False
    PAT_RECOVERY_MULTIPLIER: float = 2.0
    PAT_ADAPTIVE_STAKING: bool = True
    PAT_ADAPTIVE_WINDOW: int = 20

    # ═══════════════════════════════════════════════════════════════
    #  CONNECTION — WebSocket & Network Tuning
    # ═══════════════════════════════════════════════════════════════

    WS_PING_INTERVAL: int = 20
    WS_PING_TIMEOUT: int = 10
    WS_TIMEOUT: int = 15
    WS_MAX_SIZE: int = 2**20

    # ═══════════════════════════════════════════════════════════════
    #  BRIDGE — Internal Bridge Server
    # ═══════════════════════════════════════════════════════════════

    BRIDGE_HOST: str = "0.0.0.0"
    BRIDGE_PORT: int = 8082
    BRIDGE_URL: str = "http://localhost:8082/signal"

    # ═══════════════════════════════════════════════════════════════
    #  PUBLISHER — Signal Publisher Configuration
    # ═══════════════════════════════════════════════════════════════

    PUBLISHER_SCAN_INTERVAL: int = 900  # Seconds between scans (15 min)
    PUBLISHER_TRADE_LOG: str = str(
        Path.home() / "projects" / "1ai-trade-bot" / "data" / "trade_log.json"
    )  # noqa: E501

    # ═══════════════════════════════════════════════════════════════
    #  TELEGRAM — Notification Service
    # ═══════════════════════════════════════════════════════════════

    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""

    # ═══════════════════════════════════════════════════════════════
    #  STORAGE — Data Persistence
    # ═══════════════════════════════════════════════════════════════

    DATA_DIR: str = str(Path.home() / "projects" / "1ai-trade-bot" / "data")
    STORAGE_DB_PATH: str = ""  # Override for DB path (defaults to DATA_DIR / "tradebot.db")
    DATABASE_BACKEND: str = "sqlite"  # sqlite | postgres | sqlmodel

    # ═══════════════════════════════════════════════════════════════
    #  SIGNALS — Market Data Source Configuration
    # ═══════════════════════════════════════════════════════════════

    STOCKITY_EMAIL: str = ""
    STOCKITY_PASSWORD: str = ""
    STOCKITY_AUTHTOKEN: str = ""
    STOCKITY_FULL_COOKIE: str = ""
    STOCKITY_USER_ID: str = ""
    STOCKITY_CURRENCY: str = "IDR"

    FCS_API_KEY: str = ""

    YAHOO_MIN_INTERVAL: float = 20.0

    BINANCE_BASE_URL: str = "https://api.binance.com"
    BINANCE_TIMEOUT: int = 15
    # ═══════════════════════════════════════════════════════════════
    #  PAYMENT — Tripay & Duitku Gateway Configuration
    # ═══════════════════════════════════════════════════════════════

    TRIPAY_MERCHANT_CODE: str = "T23409"
    TRIPAY_API_KEY: str = ""
    TRIPAY_PRIVATE_KEY: str = ""
    TRIPAY_BASE_URL: str = "https://tripay.co.id/api"
    TRIPAY_CALLBACK_URL: str = ""
    TRIPAY_DEFAULT_METHOD: str = "QRIS2"

    DUITKU_MERCHANT_CODE: str = "D1821"
    DUITKU_API_KEY: str = ""
    DUITKU_BASE_URL: str = "https://passport.duitku.com/webapi"
    DUITKU_CALLBACK_URL: str = ""

    # ═══════════════════════════════════════════════════════════════
    #  LLM — AI Provider Keys (LangChain-compatible)
    # ═══════════════════════════════════════════════════════════════
    OPENAI_API_KEY: str = ""
    DEEPSEEK_API_KEY: str = ""
    GEMINI_API_KEY: str = ""
    GROK_API_KEY: str = ""
    GEMINI_BACKUP_KEYS: str = ""  # comma-separated fallback keys

    LLM_PREFERRED: str = "openai"  # "openai" | "deepseek" | "gemini"
    LLM_MODEL: str = ""  # override default model
    LLM_TEMPERATURE: float = 0.1
    PAYMENT_STORE_PATH: str = str(
        Path.home() / "projects" / "1ai-trade-bot" / "data" / "payments.json"
    )  # noqa: E501


settings = Settings()
