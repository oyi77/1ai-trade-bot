"""
Trading agent state.

Shared state for the LangGraph autonomous trading agent.
The agent loops through: observe → think → decide → execute.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict


class AgentState(TypedDict, total=False):
    """Shared state passed between graph nodes."""

    # Market state
    platform: str  # "stockity", "deriv", "mt5"
    symbol: str
    last_price: float
    last_tick_time: str
    candles: list[dict[str, Any]]  # Recent OHLCV as dicts

    # Signal state
    signal: Literal["CALL", "PUT", "HOLD"]
    signal_confidence: float
    signal_source: str  # "rule", "llm", "consensus"

    # Risk state
    daily_pnl: float
    open_positions: int
    can_trade: bool  # False if daily SL hit, etc.

    # Decision
    decision: Literal["TRADE", "SKIP", "REDUCE", "EXIT"]
    decision_reason: str
    trade_params: dict[str, Any]  # amount, direction, duration

    # Execution
    trade_id: str
    trade_status: str
    trade_platform: str
    trade_error: str
    trade_result: dict[str, Any]  # win/loss, payout

    # LLM
    llm_thought: str  # What the LLM is thinking
    messages: list[dict[str, Any]]  # Chat history
