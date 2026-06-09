#!/usr/bin/env python3
"""
Digit Backtest Replay Engine — Tick-by-Tick Historical Simulation
==================================================================

Tick-by-tick replay of historical derivative ticks. Applies Momen pattern
detection (or other pattern types) and simulates virtual martingale trades
with cash-or-nothing settlement. All results are persisted to SQLite.

Usage:
    from tradebot.brokers.deriv.backtest import DigitBacktestEngine
    engine = DigitBacktestEngine(client=my_client)
    summary = await engine.run(pattern_type='Momen', symbol='R_75', count=500)
"""

import logging
import sqlite3
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from tradebot.brokers.deriv.client import DerivTick, DerivWSClient
from tradebot.brokers.deriv.config import (
    INITIAL_STAKE,
    MAX_OPS,
    MAX_STAKE_MULTIPLIER,
    MIN_CONFIDENCE,
    PAYOUT_MULTIPLIER,
    STAKE_MULTIPLIER,
    TICK_HISTORY,
)
from tradebot.brokers.deriv.patterns import MomenPatternAnalyzer
from tradebot.config import settings
from tradebot.storage.cognitive import CognitiveDB

LOG = logging.getLogger("tradebot.brokers.deriv.backtest")

# ── Data Paths ──
BACKTEST_DIR = Path(settings.DATA_DIR) / "deriv"
BACKTEST_DB = BACKTEST_DIR / "backtest_results.db"


# ── Trade Record ──

@dataclass
class TradeRecord:
    tick_time: str
    digit: int
    stake: float
    outcome: str
    pnl: float
    cycle_id: int
    op_number: int
    symbol: str
    pattern_type: str


@dataclass
class BacktestSummary:
    total_cycles: int = 0
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    net_pnl: float = 0.0
    profit_factor: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    max_win: float = 0.0
    max_loss: float = 0.0
    max_consecutive_losses: int = 0
    duration_seconds: float = 0.0
    ticks_processed: int = 0
    patterns_found: int = 0


# ── Database ──

def _init_backtest_db():
    BACKTEST_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(BACKTEST_DB))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS backtest_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            pattern_type TEXT NOT NULL,
            ticks_fetched INTEGER,
            cycles_completed INTEGER,
            total_trades INTEGER,
            net_pnl REAL,
            win_rate REAL,
            duration_ms INTEGER
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS backtest_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            tick_time TEXT NOT NULL,
            digit INTEGER NOT NULL,
            stake REAL NOT NULL,
            outcome TEXT NOT NULL,
            pnl REAL NOT NULL,
            cycle_id INTEGER NOT NULL,
            op_number INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            pattern_type TEXT NOT NULL,
            FOREIGN KEY (run_id) REFERENCES backtest_runs(id)
        )
    """)
    conn.commit()
    conn.close()
    LOG.info("🗄️  Backtest DB ready: %s", BACKTEST_DB)


def _save_run_meta(timestamp: str, symbol: str, pattern_type: str,
                   ticks_fetched: int, summary: BacktestSummary) -> int:
    conn = sqlite3.connect(str(BACKTEST_DB))
    conn.execute(
        """INSERT INTO backtest_runs
           (timestamp, symbol, pattern_type, ticks_fetched, cycles_completed,
            total_trades, net_pnl, win_rate, duration_ms)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (timestamp, symbol, pattern_type, ticks_fetched,
         summary.total_cycles, summary.total_trades,
         round(summary.net_pnl, 2), round(summary.win_rate, 2),
         int(summary.duration_seconds * 1000)),
    )
    run_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    conn.close()
    return run_id


def _save_trades(run_id: int, trades: list[TradeRecord]):
    conn = sqlite3.connect(str(BACKTEST_DB))
    rows = [
        (run_id, t.tick_time, t.digit, round(t.stake, 4),
         t.outcome, round(t.pnl, 4), t.cycle_id, t.op_number,
         t.symbol, t.pattern_type)
        for t in trades
    ]
    conn.executemany(
        """INSERT INTO backtest_trades
           (run_id, tick_time, digit, stake, outcome, pnl,
            cycle_id, op_number, symbol, pattern_type)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        rows,
    )
    conn.commit()
    conn.close()
    LOG.info("💾  Saved %d trades to DB (run_id=%d)", len(trades), run_id)


# ── Pattern Factory ──

def _make_analyzer(pattern_type: str = "Momen"):
    pattern_type = pattern_type.lower()
    if pattern_type == "momen":
        return MomenPatternAnalyzer(analysis_ticks=TICK_HISTORY)
    elif pattern_type == "adjacency":
        from tradebot.brokers.deriv.patterns import AdjacencyPatternAnalyzer
        return AdjacencyPatternAnalyzer()
    elif pattern_type == "streak":
        from tradebot.brokers.deriv.patterns import StreakCountdownAnalyzer
        return StreakCountdownAnalyzer()
    else:
        raise ValueError(f"Unknown pattern_type: {pattern_type}. "
                         f"Supported: Momen, Adjacency, Streak")


# ── Payout Calculator ──

def _compute_pnl(stake: float, won: bool) -> float:
    if won:
        payout = stake * PAYOUT_MULTIPLIER
        return round(payout - stake, 4)
    return round(-stake, 4)


# ── Engine ──

class DigitBacktestEngine:
    """Tick-by-tick backtest replay engine for digit trading strategies."""

    def __init__(self, client: DerivWSClient | None = None):
        self.client = client
        self._trades: list[TradeRecord] = []
        self._cycle_id = 0
        self._consecutive_losses = 0

    async def run(self, pattern_type: str = "Momen", symbol: str = "R_75",
                  count: int = 500) -> BacktestSummary:
        start_time = time.time()
        timestamp = datetime.now(UTC).isoformat()
        self._trades.clear()
        self._cycle_id = 0
        self._consecutive_losses = 0

        LOG.info("🚀 Backtest start: pattern=%s symbol=%s ticks=%d",
                 pattern_type, symbol, count)

        ticks = await self._fetch_ticks(symbol, count)
        if not ticks:
            LOG.error("❌ No ticks fetched — aborting")
            return BacktestSummary()

        LOG.info("📈  Fetched %d ticks", len(ticks))

        analyzer = _make_analyzer(pattern_type)
        lookback = getattr(analyzer, "analysis_ticks", getattr(analyzer, "lookback", TICK_HISTORY))
        patterns_found = 0
        i = lookback

        while i < len(ticks) - 1:
            window = ticks[i - lookback:i]
            analysis = analyzer.analyze(window)
            if analysis:
                patterns_found += 1
                confidence = getattr(analysis, "confidence", 1.0)
                if confidence >= MIN_CONFIDENCE:
                    consumed = await self._execute_martingale_cycle(
                        ticks=ticks, start_idx=i,
                        symbol=symbol, pattern_type=pattern_type,
                        analyzer_type=type(analyzer).__name__,
                        analysis=analysis,
                    )
                    i += consumed
                    continue
            i += 1

        elapsed = time.time() - start_time
        summary = self._aggregate(ticks_processed=len(ticks),
                                  patterns_found=patterns_found,
                                  duration_seconds=elapsed)

        _init_backtest_db()
        run_id = _save_run_meta(timestamp, symbol, pattern_type,
                                len(ticks), summary)
        if self._trades:
            _save_trades(run_id, self._trades)

        LOG.info("✅ Backtest complete: %d cycles, %d trades, PnL=$%.2f (%.1f%%)",
                 summary.total_cycles, summary.total_trades,
                 summary.net_pnl, summary.win_rate * 100)

        return summary

    async def _fetch_ticks(self, symbol: str, count: int) -> list[DerivTick]:
        if self.client is None:
            raise ValueError("DerivWSClient required — pass client= to constructor")

        connected = self.client.is_connected
        if not connected:
            await self.client.connect()

        try:
            batch_size = min(count, 5000)
            ticks = await self.client.get_ticks_history(symbol, count=batch_size)
            if not ticks:
                LOG.warning("No ticks returned from API")
                return []

            if count > batch_size and len(ticks) >= batch_size:
                all_ticks = list(ticks)
                while len(all_ticks) < count:
                    end_epoch = all_ticks[0].epoch - 1
                    more = await self.client.get_ticks_history(
                        symbol, end=str(end_epoch), count=batch_size
                    )
                    if not more:
                        break
                    all_ticks = more + all_ticks
                    LOG.debug("  Paginated: %d total ticks", len(all_ticks))
                    if len(more) < batch_size:
                        break
                ticks = all_ticks

            return ticks
        finally:
            if not connected:
                await self.client.disconnect()

    async def _execute_martingale_cycle(
        self, ticks: list[DerivTick], start_idx: int,
        symbol: str, pattern_type: str, analyzer_type: str,
        analysis=None,
    ) -> int:
        self._cycle_id += 1
        stake = INITIAL_STAKE
        consumed = 0
        idx = start_idx

        predicted_digit = getattr(analysis, 'predicted_digit', 7)
        if predicted_digit < 0 or predicted_digit > 9:
            predicted_digit = 7

        for op in range(1, MAX_OPS + 1):
            if idx + 1 >= len(ticks):
                break

            entry_tick = ticks[idx]
            settlement_tick = ticks[idx + 1]

            won = settlement_tick.digit == predicted_digit
            pnl = _compute_pnl(stake, won)

            record = TradeRecord(
                tick_time=datetime.fromtimestamp(entry_tick.epoch, tz=UTC).isoformat(),
                digit=predicted_digit,
                stake=stake,
                outcome="WIN" if won else "LOSS",
                pnl=pnl,
                cycle_id=self._cycle_id,
                op_number=op,
                symbol=symbol,
                pattern_type=pattern_type,
            )
            self._trades.append(record)

            if won:
                self._consecutive_losses = 0
                consumed += 2
                LOG.debug("  🟢 Cycle %d OP%d: WIN +$%.2f (stake=$%.2f)",
                          self._cycle_id, op, pnl, stake)
                CognitiveDB.record_pattern_result(
                    symbol, f"{pattern_type}-d{predicted_digit}", True
                )
                CognitiveDB.record_market_result(symbol, True)
                break
            else:
                self._consecutive_losses += 1
                consumed += 2
                next_stake = round(stake * STAKE_MULTIPLIER, 4)
                LOG.debug("  🔴 Cycle %d OP%d: LOSS -$%.2f (stake=$%.2f → $%.2f)",
                          self._cycle_id, op, -stake, stake, next_stake)
                stake = min(next_stake, INITIAL_STAKE * MAX_STAKE_MULTIPLIER)
                idx += 1
                CognitiveDB.record_pattern_result(
                    symbol, f"{pattern_type}-d{predicted_digit}", False
                )
                CognitiveDB.record_market_result(symbol, False)

                if op == MAX_OPS:
                    consumed += 0
                    LOG.debug("  💀 Cycle %d: MAX_OPS hit, sequence reset", self._cycle_id)
                    break

        return consumed

    def _aggregate(self, ticks_processed: int, patterns_found: int,
                   duration_seconds: float) -> BacktestSummary:
        if not self._trades:
            return BacktestSummary(ticks_processed=ticks_processed,
                                   patterns_found=patterns_found,
                                   duration_seconds=duration_seconds)

        wins = [t for t in self._trades if t.outcome == "WIN"]
        losses = [t for t in self._trades if t.outcome == "LOSS"]

        total_trades = len(self._trades)
        total_wins = len(wins)
        total_losses = len(losses)
        win_rate = total_wins / total_trades if total_trades > 0 else 0.0

        gross_profit = sum(t.pnl for t in wins)
        gross_loss = abs(sum(t.pnl for t in losses))
        net_pnl = gross_profit - gross_loss

        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")

        avg_win = gross_profit / total_wins if total_wins > 0 else 0.0
        avg_loss = gross_loss / total_losses if total_losses > 0 else 0.0
        max_win = max((t.pnl for t in wins), default=0.0)
        max_loss = min((t.pnl for t in losses), default=0.0)

        unique_cycles = len({t.cycle_id for t in self._trades})

        return BacktestSummary(
            total_cycles=unique_cycles,
            total_trades=total_trades,
            wins=total_wins,
            losses=total_losses,
            win_rate=win_rate,
            gross_profit=round(gross_profit, 2),
            gross_loss=round(gross_loss, 2),
            net_pnl=round(net_pnl, 2),
            profit_factor=round(profit_factor, 4),
            avg_win=round(avg_win, 4),
            avg_loss=round(avg_loss, 4),
            max_win=round(max_win, 4),
            max_loss=round(max_loss, 4),
            max_consecutive_losses=self._consecutive_losses,
            duration_seconds=round(duration_seconds, 2),
            ticks_processed=ticks_processed,
            patterns_found=patterns_found,
        )


# ── Pretty Printer ──

def print_summary(summary: BacktestSummary):
    """Print a formatted summary."""
    print()
    print("═" * 50)
    print("📊  BACKTEST SUMMARY")
    print("═" * 50)
    print(f"  Duration:          {summary.duration_seconds:.1f}s")
    print(f"  Ticks processed:   {summary.ticks_processed:,}")
    print(f"  Patterns found:    {summary.patterns_found}")
    print(f"  Cycles completed:  {summary.total_cycles}")
    print(f"  Total trades:      {summary.total_trades}")
    print(f"  Wins/Losses:       {summary.wins}W / {summary.losses}L")
    print(f"  Win rate:          {summary.win_rate:.1%}")
    print(f"  Net PnL:           ${summary.net_pnl:+.2f}")
    print(f"  Profit factor:     {summary.profit_factor:.2f}")
    print(f"  Avg win:           ${summary.avg_win:+.4f}")
    print(f"  Avg loss:          ${summary.avg_loss:.4f}")
    print(f"  Max win:           ${summary.max_win:+.4f}")
    print(f"  Max loss:          ${summary.max_loss:.4f}")
    print(f"  Max cons. losses:  {summary.max_consecutive_losses}")
    print("═" * 50)
    print()
