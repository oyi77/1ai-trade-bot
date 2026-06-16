"""
Base Analyzer — Abstract interface for all Hybrid Engine analyzers.
===============================================================
Every analyzer (LSTM, ZF-Core, Market Integrity) inherits from this
and returns a standardized AnalysisResult.
"""
from __future__ import annotations

import time
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

logger = logging.getLogger("hybrid.analyzers")


@dataclass
class AnalysisResult:
    """Standardized output from any analyzer."""
    analyzer: str                    # "lstm" | "zf_core" | "integrity"
    action: Optional[str] = None     # "BUY" | "SELL" | "HOLD" | None
    confidence: float = 0.0          # 0.0 — 1.0
    blocked: bool = False            # True = signal must NOT proceed
    block_reason: str = ""           # Why it was blocked
    reasoning: str = ""              # Human-readable explanation
    metadata: dict = field(default_factory=dict)  # Analyzer-specific data
    execution_time_ms: float = 0.0   # How long the analysis took
    success: bool = True             # False = analyzer crashed/error
    error: str = ""                  # Error message if success=False

    def to_dict(self) -> dict:
        return {
            "analyzer": self.analyzer,
            "action": self.action,
            "confidence": round(self.confidence, 4),
            "blocked": self.blocked,
            "block_reason": self.block_reason,
            "reasoning": self.reasoning,
            "metadata": self.metadata,
            "execution_time_ms": round(self.execution_time_ms, 1),
            "success": self.success,
            "error": self.error,
        }


class BaseAnalyzer(ABC):
    """Abstract base class for all analyzers."""

    name: str = "base"

    @abstractmethod
    def analyze(self, ohlcv: pd.DataFrame, symbol: str, **kwargs) -> AnalysisResult:
        """
        Run analysis on OHLCV data.

        Args:
            ohlcv: DataFrame with columns [timestamp, open, high, low, close, volume]
            symbol: Trading symbol (e.g., "XAUUSD")
            **kwargs: Additional context (current_price, timeframe, etc.)

        Returns:
            AnalysisResult with action, confidence, and reasoning.
        """
        ...

    def safe_analyze(self, ohlcv: pd.DataFrame, symbol: str, **kwargs) -> AnalysisResult:
        """
        Wraps analyze() with timing and exception handling.
        Never raises — always returns an AnalysisResult (even on crash).
        """
        start = time.monotonic()
        try:
            result = self.analyze(ohlcv, symbol, **kwargs)
            result.execution_time_ms = (time.monotonic() - start) * 1000
            result.success = True
            return result
        except Exception as e:
            elapsed = (time.monotonic() - start) * 1000
            logger.error("💥 %s CRASH (%.0fms): %s", self.name, elapsed, e, exc_info=True)
            return AnalysisResult(
                analyzer=self.name,
                success=False,
                error=str(e),
                execution_time_ms=elapsed,
            )
