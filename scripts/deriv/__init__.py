#!/usr/bin/env python3
"""
Deriv Trading Package — Unified Module
=======================================

Single source of truth for all Deriv.com trading logic.
Absorbs: deriv_client.py, deriv-digit-match-bot/deriv-actuary/
Fixes: old ~/.openclaw/workspace/ paths → ~/projects/1ai-trade-bot/

Capabilities:
  - DerivWSClient: async WebSocket auth, ticks, proposals, buy/sell
  - MomenPatternAnalyzer: carrier digit → 7 pattern (course bot)
  - AdjacencyPatternAnalyzer: trigger→target adjacency (actuary v4)
  - StreakCountdownStrategy: streak-based countdown trigger (GUI v5.9)
  - DigitMartingaleStrategy: full 3-OP martingale w/ Config L risk
  - MultiStreamActuary: parallel WS readers for multi-symbol (actuary v4)
  - CognitiveDB: self-learning pattern optimization
  - Persistence: SQLite state, paper mode, reconciliation
"""

from .client import DerivWSClient, DerivTick, DerivOHLCV, DerivContractResult
from .patterns import MomenPatternAnalyzer, MomenAnalysis, \
    AdjacencyPatternAnalyzer, AdjacencyAnalysis, \
    StreakCountdownAnalyzer, StreakAnalysis
from .strategy import DigitMartingaleStrategy, TradeResult
from .actuary import MultiStreamActuary, CognitiveDB
from .config import SYNTHETIC_INDICES, CONTRACT_TYPES, \
    DEFAULT_SYMBOL, DEFAULT_STAKE, DAILY_TP, DAILY_SL

__all__ = [
    "DerivWSClient", "DerivTick", "DerivOHLCV", "DerivContractResult",
    "MomenPatternAnalyzer", "MomenAnalysis",
    "AdjacencyPatternAnalyzer", "AdjacencyAnalysis",
    "StreakCountdownAnalyzer", "StreakAnalysis",
    "DigitMartingaleStrategy", "TradeResult",
    "MultiStreamActuary", "CognitiveDB",
    "SYNTHETIC_INDICES", "CONTRACT_TYPES",
    "DEFAULT_SYMBOL", "DEFAULT_STAKE", "DAILY_TP", "DAILY_SL",
]
