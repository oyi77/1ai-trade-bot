#!/usr/bin/env python3
"""
Deriv Trading Strategies — Risk-Managed Execution
=================================================

DigitMartingaleStrategy:
  - Momen 1/2 pattern entry
  - 3-OP martingale progression (1.55x multiplier)
  - Config L risk limits (SL $8, TP $5, RR 1:1.625)
  - Stake capped at 10x initial to prevent runaway
  - Session-based profit/loss tracking
"""

import asyncio
import logging
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional

from .client import DerivWSClient
from .patterns import MomenPatternAnalyzer, MomenAnalysis
from .config import (
    DAILY_TP, DAILY_SL, INITIAL_STAKE, STAKE_MULTIPLIER,
    MAX_OPS, MAX_STAKE_MULTIPLIER, TICK_HISTORY,
    DEFAULT_SYMBOL, DEFAULT_CONTRACT_TYPE, DEFAULT_BARRIER,
    MIN_CONFIDENCE,
)

LOG = logging.getLogger("deriv.strategy")


@dataclass
class TradeResult:
    profit: float
    total_stake: float
    trades: int
    wins: int
    losses: int
    win_rate: float
    cycles: int
    stopped_early: bool = False
    reason: str = ""


class DigitMartingaleStrategy:
    """Digit trading with Momen 1/2 entry + martingale progression.

    Risk Model (Config L — verified 8 Jun 2026):
      WR 39.8%, PF 1.62, +516 pips over 334 trades (6mo backtest)
      SL: 32pt → $8.00 stop loss
      TP: 52pt → $5.00 profit target
      RR: 1:1.625
    """

    def __init__(self, client: DerivWSClient,
                 symbol: str = DEFAULT_SYMBOL,
                 contract_type: str = DEFAULT_CONTRACT_TYPE,
                 barrier: int = DEFAULT_BARRIER,
                 initial_stake: float = INITIAL_STAKE,
                 stake_multiplier: float = STAKE_MULTIPLIER,
                 max_ops: int = MAX_OPS,
                 target_profit: float = DAILY_TP,
                 max_loss: float = DAILY_SL,
                 analysis_ticks: int = TICK_HISTORY,
                 min_confidence: float = MIN_CONFIDENCE,
                 duration: int = 1):
        self.client = client
        self.symbol = symbol
        self.contract_type = contract_type
        self.barrier = barrier
        self.initial_stake = initial_stake
        self.stake_multiplier = stake_multiplier
        self.max_ops = max_ops
        self.target_profit = target_profit
        self.max_loss = max_loss
        self.analysis_ticks = analysis_ticks
        self.min_confidence = min_confidence
        self.duration = duration

        self.analyzer = MomenPatternAnalyzer(analysis_ticks=analysis_ticks)
        self.balance = 0.0
        self.start_balance = 0.0
        self.total_wins = 0
        self.total_losses = 0
        self.cycle_count = 0
        self.running = False

    async def get_session_balance(self) -> float:
        """Fetch current balance from Deriv."""
        bal = await self.client.get_balance()
        if bal is not None:
            self.balance = bal
        return self.balance

    async def analyse_and_trade(self) -> TradeResult:
        """Run one complete analysis → trade cycle.

        1. Collect ticks for Momen pattern analysis
        2. If valid pattern found → execute martingale cycle
        3. Respect SL/TP limits
        """
        self.running = True
        self.start_balance = await self.get_session_balance()
        LOG.info("🔄 Cycle %d | Balance: $%.2f", self.cycle_count + 1, self.start_balance)

        # Collect ticks
        ticks = await self.client.get_ticks_history(self.symbol, count=self.analysis_ticks)
        if len(ticks) < self.analysis_ticks:
            LOG.warning("Not enough ticks: %d/%d", len(ticks), self.analysis_ticks)
            self.running = False
            return TradeResult(0, 0, 0, 0, 0, 0, self.cycle_count + 1,
                               stopped_early=True, reason="insufficient_ticks")

        # Analyze pattern
        analysis = self.analyzer.analyze(ticks)
        if not analysis:
            LOG.info("No valid Momen pattern found, skipping")
            self.cycle_count += 1
            self.running = False
            return TradeResult(0, 0, 0, 0, 0, 0, self.cycle_count + 1,
                               stopped_early=True, reason="no_pattern")

        LOG.info("🎯 Pattern: carrier=%d M1@tick=%d M2@tick=%d confidence=%.0f%%",
                 analysis.carrier, analysis.momen1_tick, analysis.momen2_tick,
                 analysis.confidence * 100)

        if analysis.confidence < self.min_confidence:
            LOG.info("Confidence too low (%.0f%%), skipping", analysis.confidence * 100)
            self.cycle_count += 1
            self.running = False
            return TradeResult(0, 0, 0, 0, 0, 0, self.cycle_count + 1,
                               stopped_early=True, reason="low_confidence")

        # Execute martingale
        return await self._execute_cycle(analysis)

    async def _execute_cycle(self, analysis: MomenAnalysis) -> TradeResult:
        """Execute martingale progression after pattern confirmation."""
        stake = self.initial_stake
        total_stake = 0.0
        trades = 0
        wins = 0
        losses = 0
        stopped_early = False
        stop_reason = ""

        for op in range(1, self.max_ops + 1):
            # Check profit/loss limits
            profit_run = self.balance - self.start_balance
            if profit_run >= self.target_profit:
                LOG.info("✅ TP HIT: +$%.2f >= $%.2f", profit_run, self.target_profit)
                stopped_early = True
                stop_reason = "tp_hit"
                break
            if profit_run <= self.max_loss:
                LOG.info("🛑 SL HIT: -$%.2f <= -$%.2f", abs(profit_run), abs(self.max_loss))
                stopped_early = True
                stop_reason = "sl_hit"
                break

            LOG.info(f"   📍 OP {op}/{self.max_ops} stake=${stake:.2f}")

            result = await self.client.buy_digit(
                symbol=self.symbol,
                contract_type=self.contract_type,
                barrier=self.barrier,
                stake=stake,
                duration=self.duration,
            )

            total_stake += stake
            trades += 1

            if result:
                await asyncio.sleep(1)   # Wait for settlement
                await self.get_session_balance()
                profit_run = self.balance - self.start_balance

                if profit_run > 0:
                    LOG.info("   ✅ WIN! Current P/L: +$%.2f", profit_run)
                    wins += 1
                    stake = self.initial_stake
                else:
                    LOG.info("   ❌ LOSS! Martingale: $%.2f -> $%.2f",
                             stake, stake * self.stake_multiplier)
                    losses += 1
                    stake = round(stake * self.stake_multiplier, 2)
                    await asyncio.sleep(2)
            else:
                losses += 1
                stake = round(stake * self.stake_multiplier, 2)

            # Guard: cap stake to prevent runaway
            if stake > self.initial_stake * MAX_STAKE_MULTIPLIER:
                LOG.warning("Stake capped at $%.2f", self.initial_stake * MAX_STAKE_MULTIPLIER)
                stake = self.initial_stake * MAX_STAKE_MULTIPLIER

        # Final balance
        await self.get_session_balance()
        final_profit = round(self.balance - self.start_balance, 2)
        self.total_wins += wins
        self.total_losses += losses
        self.cycle_count += 1
        win_rate = (wins / trades * 100) if trades > 0 else 0.0
        self.running = False

        LOG.info("📊 Cycle %d done: +$%.2f (%d/%d wins, %.0f%%)",
                 self.cycle_count, final_profit, wins, trades, win_rate)

        return TradeResult(
            profit=final_profit,
            total_stake=round(total_stake, 2),
            trades=trades,
            wins=wins,
            losses=losses,
            win_rate=win_rate,
            cycles=self.cycle_count,
            stopped_early=stopped_early,
            reason=stop_reason,
        )
