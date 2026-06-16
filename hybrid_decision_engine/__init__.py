"""
Hybrid Decision Engine — Vilona AI Trading Ecosystem
=====================================================
Advanced multi-analyzer signal generation with cross-validation.

Architecture:
  MT5 Bridge → Data Fetcher → 3 Parallel Analyzers → Signal Generator → Decision Engine
                                                                          ↓
                                                              Channel Broadcast (Telegram)

Phase 1: API Setup & Data Fetcher
Port: 8770 (FastAPI)
"""

__version__ = "1.0.0"
__phase__ = "Phase 1 — API Setup & Data Fetcher"
