#!/usr/bin/env python3
"""
Digit Backtest Replay Engine — Tick-by-Tick Historical Simulation
==================================================================

Tick-by-tick replay of historical derivative ticks. Applies Momen pattern
detection (or other pattern types) and simulates virtual martingale trades
with cash-or-nothing settlement. All results are persisted to SQLite.

Usage:
    python -m scripts.deriv.backtest                     # Quick run (500 ticks)
    python -m scripts.deriv.backtest R_75 Momen 2000     # Custom run
"""

import asyncio
import logging
import sqlite3
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .client import DerivWSClient, DerivTick
from .config import (
    INITIAL_STAKE, STAKE_MULTIPLIER, MAX_OPS, MAX_STAKE_MULTIPLIER,
    PAYOUT_MULTIPLIER, TICK_HISTORY, MIN_CONFIDENCE,
)
from .patterns import MomenPatternAnalyzer
from .actuary import CognitiveDB

LOG = logging.getLogger("deriv.backtest")

# ── Data Paths ──
BACKTEST_DIR = Path.home() / "projects" / "1ai-trade-bot" / "data" / "deriv"
BACKTEST_DB = BACKTEST_DIR / "backtest_results.db"


# ── Trade Record ──

@dataclass
class TradeRecord:
    """A single virtual trade produced by the backtest engine."""
    tick_time: str            # ISO timestamp of the entry tick
    digit: int                # Predicted digit from pattern analysis
    stake: float              # Stake placed on this trade
    outcome: str              # 'WIN' or 'LOSS'
    pnl: float                # Profit/loss (payout - stake, or -stake)
    cycle_id: int             # Martingale cycle ID
    op_number: int            # OP number within the cycle
    symbol: str               # Symbol traded
    pattern_type: str         # Pattern type used


@dataclass
class BacktestSummary:
    """Aggregated results from a backtest run."""
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
    """Create backtest results tables if they don't exist."""
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
    """Insert a run record and return its ID."""
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
    """Batch-insert trade records."""
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
    """Create a pattern analyzer for the given type."""
    pattern_type = pattern_type.lower()
    if pattern_type == "momen":
        return MomenPatternAnalyzer(analysis_ticks=TICK_HISTORY)
    elif pattern_type == "adjacency":
        from .patterns import AdjacencyPatternAnalyzer
        return AdjacencyPatternAnalyzer()
    elif pattern_type == "streak":
        from .patterns import StreakCountdownAnalyzer
        return StreakCountdownAnalyzer()
    else:
        raise ValueError(f"Unknown pattern_type: {pattern_type}. "
                         f"Supported: Momen, Adjacency, Streak")


# ── Payout Calculator ──

def _compute_pnl(stake: float, won: bool) -> float:
    """Compute profit/loss for a DIGITMATCH trade.

    Payout is ~8.33x stake (from PAYOUT_MULTIPLIER config).
    Net profit = payout - stake if win, -stake if loss.
    """
    if won:
        payout = stake * PAYOUT_MULTIPLIER
        return round(payout - stake, 4)
    return round(-stake, 4)


# ── Engine ──

class DigitBacktestEngine:
    """Tick-by-tick backtest replay engine for digit trading strategies.

    Fetches historical ticks, replays them one at a time through a sliding
    window pattern analyzer, simulates virtual martingale trades, and
    records every result to SQLite.

    Usage:
        engine = DigitBacktestEngine(client=my_client)
        summary = await engine.run(pattern_type='Momen', symbol='R_75', count=500)
        print(summary)
    """

    def __init__(self, client: Optional[DerivWSClient] = None):
        self.client = client
        self._trades: list[TradeRecord] = []
        self._cycle_id = 0
        self._consecutive_losses = 0

    # ── Public API ──

    async def run(self, pattern_type: str = "Momen", symbol: str = "R_75",
                  count: int = 500) -> BacktestSummary:
        """Run a full backtest replay.

        Args:
            pattern_type: Pattern strategy to apply ('Momen').
            symbol: Deriv synthetic index symbol (default R_75).
            count: Number of ticks to fetch (default 500, use higher for
                   more data e.g. 5000).

        Returns:
            BacktestSummary with aggregated stats.
        """
        start_time = time.time()
        timestamp = datetime.now(timezone.utc).isoformat()
        self._trades.clear()
        self._cycle_id = 0
        self._consecutive_losses = 0

        LOG.info("🚀 Backtest start: pattern=%s symbol=%s ticks=%d",
                 pattern_type, symbol, count)

        # 1. Fetch ticks
        ticks = await self._fetch_ticks(symbol, count)
        if not ticks:
            LOG.error("❌ No ticks fetched — aborting")
            return BacktestSummary()

        LOG.info("📈  Fetched %d ticks", len(ticks))

        # 2. Build analyzer
        analyzer = _make_analyzer(pattern_type)

        # 3. Step through ticks
        lookback = getattr(analyzer, "analysis_ticks", getattr(analyzer, "lookback", TICK_HISTORY))
        patterns_found = 0
        i = lookback

        while i < len(ticks) - 1:
            # Sliding window: ticks[i-lookback:i]
            window = ticks[i - lookback:i]

            # Run pattern detection
            analysis = analyzer.analyze(window)
            if analysis:
                patterns_found += 1
                confidence = getattr(analysis, "confidence", 1.0)
                if confidence >= MIN_CONFIDENCE:
                    # Execute a martingale cycle starting at this tick
                    consumed = await self._execute_martingale_cycle(
                        ticks=ticks, start_idx=i,
                        symbol=symbol, pattern_type=pattern_type,
                        analyzer_type=type(analyzer).__name__,
                        analysis=analysis,
                    )
                    i += consumed
                    continue

            i += 1

        # 4. Build summary
        elapsed = time.time() - start_time
        summary = self._aggregate(ticks_processed=len(ticks),
                                  patterns_found=patterns_found,
                                  duration_seconds=elapsed)

        # 5. Save to DB
        _init_backtest_db()
        run_id = _save_run_meta(timestamp, symbol, pattern_type,
                                len(ticks), summary)
        if self._trades:
            _save_trades(run_id, self._trades)

        LOG.info("✅ Backtest complete: %d cycles, %d trades, PnL=$%.2f (%.1f%%)",
                 summary.total_cycles, summary.total_trades,
                 summary.net_pnl, summary.win_rate * 100)

        return summary

    # ── Internal: fetch ticks ──

    async def _fetch_ticks(self, symbol: str, count: int) -> list[DerivTick]:
        """Fetch tick history from Deriv API or load from cache."""
        if self.client is None:
            raise ValueError("DerivWSClient required — pass client= to constructor")

        connected = self.client.is_connected
        if not connected:
            await self.client.connect()

        try:
            # get_ticks_history max is usually 5000 per call
            batch_size = min(count, 5000)
            ticks = await self.client.get_ticks_history(symbol, count=batch_size)
            if not ticks:
                LOG.warning("No ticks returned from API")
                return []

            # If we need more ticks, paginate backwards
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

    # ── Internal: martingale cycle ──

    async def _execute_martingale_cycle(
        self, ticks: list[DerivTick], start_idx: int,
        symbol: str, pattern_type: str, analyzer_type: str,
        analysis=None,
    ) -> int:
        """Run one martingale progression starting from start_idx.

        Uses the predicted_digit from the pattern analysis result to
        determine which digit to trade (instead of hardcoding it).

        For each OP:
          - Place stake predicting the digit from analysis
          - Settle on next tick's digit
          - If win → break (reset stake)
          - If loss → multiply stake, continue (up to MAX_OPS)

        Returns:
            Number of ticks consumed (including settlement).
        """
        self._cycle_id += 1
        stake = INITIAL_STAKE
        consumed = 0
        idx = start_idx  # current tick index where pattern was detected

        # Extract predicted digit from analysis, fallback to 7 for Momen
        predicted_digit = getattr(analysis, 'predicted_digit', 7)
        if predicted_digit < 0 or predicted_digit > 9:
            predicted_digit = 7  # safety fallback

        for op in range(1, MAX_OPS + 1):
            # We need at least 2 ticks ahead: 1 (entry tick) + 1 (settlement)
            if idx + 1 >= len(ticks):
                break

            entry_tick = ticks[idx]
            settlement_tick = ticks[idx + 1]

            # Determine outcome: use predicted_digit from pattern analysis
            won = settlement_tick.digit == predicted_digit
            pnl = _compute_pnl(stake, won)

            # Record trade
            record = TradeRecord(
                tick_time=datetime.fromtimestamp(entry_tick.epoch, tz=timezone.utc).isoformat(),
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
                # Cycle complete — reset stake
                self._consecutive_losses = 0
                consumed += 2  # entry + settlement tick consumed
                LOG.debug("  🟢 Cycle %d OP%d: WIN +$%.2f (stake=$%.2f)",
                          self._cycle_id, op, pnl, stake)
                # Record to cognitive memory
                CognitiveDB.record_pattern_result(
                    symbol, f"{pattern_type}-d{predicted_digit}", True
                )
                CognitiveDB.record_market_result(symbol, True)
                break
            else:
                # Loss — progress martingale
                self._consecutive_losses += 1
                consumed += 2  # entry + settlement tick consumed
                next_stake = round(stake * STAKE_MULTIPLIER, 4)
                LOG.debug("  🔴 Cycle %d OP%d: LOSS -$%.2f (stake=$%.2f → $%.2f)",
                          self._cycle_id, op, -stake, stake, next_stake)
                stake = min(next_stake, INITIAL_STAKE * MAX_STAKE_MULTIPLIER)
                idx += 1  # move forward; next op uses this settlement tick as entry
                # Record loss to cognitive memory
                CognitiveDB.record_pattern_result(
                    symbol, f"{pattern_type}-d{predicted_digit}", False
                )
                CognitiveDB.record_market_result(symbol, False)

                if op == MAX_OPS:
                    # All ops exhausted
                    consumed += 0  # already counted
                    LOG.debug("  💀 Cycle %d: MAX_OPS hit, sequence reset", self._cycle_id)
                    break
        else:
            # If the for loop didn't break (shouldn't happen with range)
            pass

        return consumed

    # ── Internal: aggregate ──

    def _aggregate(self, ticks_processed: int, patterns_found: int,
                   duration_seconds: float) -> BacktestSummary:
        """Compute summary statistics from recorded trades."""
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

        # Count cycles from unique cycle_ids
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
    """Print a formatted summary table."""
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


# ── CLI Entry ──

async def main():
    """CLI entry point for running backtests.

    Usage:
        python -m scripts.deriv.backtest                     # R_75 Momen 500
        python -m scripts.deriv.backtest R_75 Momen 2000     # Single symbol
        python -m scripts.deriv.backtest all Momen 1000      # All symbols parallel
        python -m scripts.deriv.backtest R_75,R_50 Momen 1000 # Multi-symbol parallel
    """
    import sys
    import os
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    # Ensure project root is on path
    _script_dir = Path(__file__).resolve().parent  # scripts/deriv/
    _project_root = _script_dir.parent.parent  # 1ai-trade-bot/
    for p in (str(_script_dir.parent), str(_project_root)):
        if p not in sys.path:
            sys.path.insert(0, p)

    from deriv.client import DerivWSClient

    raw_symbols = sys.argv[1] if len(sys.argv) > 1 else "R_75"
    pattern = sys.argv[2] if len(sys.argv) > 2 else "Momen"
    count = int(sys.argv[3]) if len(sys.argv) > 3 else 500

    # Parse symbol(s) — support comma-separated or "all"
    if raw_symbols.lower() == "all":
        symbols = ["R_25", "R_50", "R_75"]
    else:
        symbols = [s.strip() for s in raw_symbols.split(",")]

    pat_token = os.getenv("DERIV_PAT_TOKEN",
                          "pat_0f2c09ae7ef25d3970e5829982e77206bd53c761c57e153f53dd99f8e1d11bb2")
    app_id = os.getenv("DERIV_APP_ID", "33uQ6fU4eIRvJc6jkYeEa")
    account_id = os.getenv("DERIV_ACCOUNT_ID", "DOT92925029")

    results = {}
    for symbol in symbols:
        LOG.info("═══ Running backtest for %s ═══", symbol)
        client = DerivWSClient(pat_token=pat_token, app_id=app_id, account_id=account_id)
        engine = DigitBacktestEngine(client=client)
        summary = await engine.run(pattern_type=pattern, symbol=symbol, count=count)
        print_summary(summary)
        results[symbol] = summary

    # Print cross-symbol comparison
    if len(results) > 1:
        print()
        print("═" * 60)
        print("📊  CROSS-SYMBOL COMPARISON")
        print("═" * 60)
        print(f"  {'Symbol':<10} {'Cycles':<8} {'Trades':<8} {'WR':<8} {'PnL':<10} {'PF':<8}")
        print(f"  {'-'*8:<10} {'-'*6:<8} {'-'*6:<8} {'-'*6:<8} {'-'*8:<10} {'-'*6:<8}")
        for sym, s in sorted(results.items(), key=lambda x: x[1].net_pnl, reverse=True):
            print(f"  {sym:<10} {s.total_cycles:<8} {s.total_trades:<8} "
                  f"{s.win_rate:.1%}   ${s.net_pnl:<+7.2f} {s.profit_factor:<8.2f}")
        print("═" * 60)
        print()

    return 0


if __name__ == "__main__":
    asyncio.run(main())
