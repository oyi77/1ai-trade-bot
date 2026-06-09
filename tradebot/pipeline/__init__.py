"""Pipeline orchestration — signal processing, trade execution, middleware."""

from .middleware import (
    DedupMiddleware,
    LoggingMiddleware,
    Middleware,
    MiddlewareChain,
    RateLimitMiddleware,
    RiskCheckMiddleware,
    ValidationMiddleware,
)
from .quality_gate import QualityGate
from .signal_pipeline import PipelineMetrics, SignalPipeline
from .trade_executor import TradeExecutor, TradeLifecycle

__all__ = [
    # Pipeline
    "SignalPipeline",
    "PipelineMetrics",
    "QualityGate",
    # Executor
    "TradeExecutor",
    "TradeLifecycle",
    # Middleware
    "Middleware",
    "MiddlewareChain",
    "LoggingMiddleware",
    "RateLimitMiddleware",
    "ValidationMiddleware",
    "DedupMiddleware",
    "RiskCheckMiddleware",
]
