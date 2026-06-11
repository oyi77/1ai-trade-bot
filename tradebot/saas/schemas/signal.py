"""Signal schemas"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator

SCANNABLE_SYMBOLS = [
    "BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT", "XRP/USDT",
    "ADA/USDT", "DOGE/USDT", "AVAX/USDT", "DOT/USDT", "MATIC/USDT",
    "LINK/USDT", "UNI/USDT", "ATOM/USDT", "LTC/USDT", "FIL/USDT",
]

SUPPORTED_TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h", "1d"]

SUPPORTED_INDICATORS = [
    "rsi", "macd", "ema_cross", "bollinger", "vwap",
    "stochastic", "atr", "ichimoku", "supertrend",
]


class SignalResponse(BaseModel):
    """Trading signal response"""

    id: int
    symbol: str
    signal_type: str
    status: str
    source: str = "system"
    confidence_score: float
    analysis_reason: str
    entry_price: float
    stop_loss: Optional[float]
    take_profit_1: Optional[float]
    take_profit_2: Optional[float]
    take_profit_3: Optional[float]
    risk_reward_ratio: float
    position_size_percent: float
    is_free_signal: bool
    expires_at: Optional[datetime]
    created_at: datetime

    class Config:
        from_attributes = True


class SignalListResponse(BaseModel):
    """List of signals response"""

    signals: list[SignalResponse]
    total: int
    page: int
    page_size: int
    total_pages: int
    has_next: bool
    has_prev: bool


class SignalFilterParams(BaseModel):
    """Filter parameters for signal queries"""

    symbol: Optional[str] = None
    signal_type: Optional[str] = None
    status: Optional[str] = None
    min_confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    is_free_only: Optional[bool] = None
    source: Optional[str] = None
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=100)
    sort_by: str = Field("created_at", pattern=r"^(created_at|confidence_score|symbol)$")
    sort_order: str = Field("desc", pattern=r"^(asc|desc)$")


# ── Signal Generator / Scanner ────────────────────────────────────


class GenerateSignalRequest(BaseModel):
    """User requests a custom signal for a specific symbol"""

    symbol: str
    timeframe: str = Field("1h", description="Candlestick timeframe")
    indicators: list[str] = Field(
        default=["rsi", "macd", "ema_cross"],
        min_length=1, max_length=5,
    )

    @field_validator("symbol")
    @classmethod
    def validate_symbol(cls, v: str) -> str:
        normalized = v.upper().strip()
        if normalized not in SCANNABLE_SYMBOLS:
            raise ValueError(
                f"Unsupported symbol. Choose from: {', '.join(SCANNABLE_SYMBOLS)}"
            )
        return normalized

    @field_validator("timeframe")
    @classmethod
    def validate_timeframe(cls, v: str) -> str:
        if v not in SUPPORTED_TIMEFRAMES:
            raise ValueError(
                f"Unsupported timeframe. Choose from: {', '.join(SUPPORTED_TIMEFRAMES)}"
            )
        return v

    @field_validator("indicators")
    @classmethod
    def validate_indicators(cls, v: list[str]) -> list[str]:
        clean = [ind.lower().strip() for ind in v]
        invalid = [ind for ind in clean if ind not in SUPPORTED_INDICATORS]
        if invalid:
            raise ValueError(
                f"Unsupported indicators: {', '.join(invalid)}. "
                f"Choose from: {', '.join(SUPPORTED_INDICATORS)}"
            )
        return clean


class ScanMarketsRequest(BaseModel):
    """Scan multiple symbols and return top signals"""

    symbols: list[str] = Field(
        default_factory=lambda: SCANNABLE_SYMBOLS[:5],
        min_length=1, max_length=15,
    )
    timeframe: str = "1h"
    min_confidence: float = Field(0.6, ge=0.0, le=1.0)
    limit: int = Field(5, ge=1, le=10)

    @field_validator("symbols")
    @classmethod
    def validate_symbols(cls, v: list[str]) -> list[str]:
        return [s.upper().strip() for s in v]

    @field_validator("timeframe")
    @classmethod
    def validate_timeframe(cls, v: str) -> str:
        if v not in SUPPORTED_TIMEFRAMES:
            raise ValueError(f"Unsupported timeframe: {v}")
        return v


class GenerationQuotaResponse(BaseModel):
    """How many signal generations the user has left"""

    tier: str
    total_credits: int
    used_credits: int
    remaining_credits: int
    bonus_credits: int
    is_unlimited: bool
    upgrade_prompt: Optional[str] = None
    donate_prompt: Optional[str] = None
