"""
BacktestEngine — tick replay backtesting engine for trading strategies.

Supports multi-symbol replay, configurable date ranges, and exports
results as structured summaries or to CSV/JSON.
"""
from __future__ import annotations

import csv
import json
import logging
import math
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tradebot.config import settings

LOG = logging.getLogger(__name__)


@dataclass
class BacktestTrade:
    """A single virtual backtest trade."""

    tick_time: str
    symbol: str
    action: str  # CALL / PUT or BUY / SELL
    price: float
    stake: float
    outcome: str  # WIN / LOSS
    pnl: float
    strategy: str = ""
    cycle_id: int = 0


@dataclass
class BacktestResult:
    """Aggregated results from a backtest run."""

    symbol: str = ""
    strategy: str = ""
    total_ticks: int = 0
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    net_pnl: float = 0.0
    profit_factor: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0
    duration_seconds: float = 0.0
    trades: list[BacktestTrade] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)


class BacktestEngine:
    """Tick replay backtesting engine.

    Reads tick data (from file, API, or provider), replays it through
    a configurable strategy callback, and records all trades and
    aggregate results.

    Usage:
        async def my_strategy(tick, context) -> Optional[dict]:
            # Return dict with action/stake or None
            ...

        engine = BacktestEngine(strategy_fn=my_strategy)
        result = await engine.run("R_75", count=5000)
        print(result.win_rate, result.net_pnl)
    """

    def __init__(
        self,
        strategy_fn: Any | None = None,
        tick_provider: Any | None = None,
        initial_balance: float = 1000.0,
    ) -> None:
        self._strategy_fn = strategy_fn
        self._tick_provider = tick_provider
        self._initial_balance = initial_balance

        self._trades: list[BacktestTrade] = []
        self._equity: list[float] = [initial_balance]
        self._cycle_id = 0
        self._consecutive_wins = 0
        self._consecutive_losses = 0
        self._balance = initial_balance
        self._peak_balance = initial_balance

    # ── Public API ──

    async def run(
        self,
        symbol: str = "R_75",
        strategy: str = "default",
        count: int = 500,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> BacktestResult:
        """Run a full backtest replay.

        Args:
            symbol: Trading symbol to backtest.
            strategy: Strategy identifier for labeling.
            count: Number of ticks to process.
            start_date: Optional ISO date filter (YYYY-MM-DD).
            end_date: Optional ISO date filter (YYYY-MM-DD).

        Returns:
            BacktestResult with aggregated stats.
        """
        start_time = time.time()
        self._reset()

        LOG.info(
            "🚀 Backtest start: symbol=%s strategy=%s ticks=%d",
            symbol, strategy, count,
        )

        # 1. Fetch ticks
        ticks = await self._fetch_ticks(symbol, count)
        if not ticks:
            LOG.error("❌ No ticks fetched — aborting")
            return BacktestResult()

        # 2. Apply date filters
        ticks = self._filter_by_date(ticks, start_date, end_date)

        LOG.info("📈  Processing %d ticks", len(ticks))

        # 3. Replay ticks through strategy
        for i, tick in enumerate(ticks):
            if self._strategy_fn is not None:
                signal = await self._evaluate_strategy(tick, i, ticks)
                if signal:
                    self._execute_trade(tick, signal, symbol, strategy)
            self._update_equity()

        # 4. Compute results
        elapsed = time.time() - start_time
        result = self._aggregate(symbol=symbol, strategy=strategy, duration=elapsed)
        result.total_ticks = len(ticks)

        LOG.info(
            "✅ Backtest complete: %d trades, PnL=$%.2f (%.1f%%)",
            result.total_trades, result.net_pnl, result.win_rate * 100,
        )

        return result

    def export_csv(self, result: BacktestResult, path: str | Path) -> None:
        """Export backtest trades to CSV."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "tick_time", "symbol", "action", "price", "stake",
                "outcome", "pnl", "strategy", "cycle_id",
            ])
            writer.writeheader()
            for trade in result.trades:
                writer.writerow(asdict(trade))
        LOG.info("📄  Exported %d trades to %s", len(result.trades), path)

    def export_json(self, result: BacktestResult, path: str | Path) -> None:
        """Export full backtest result to JSON."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "metadata": {
                "symbol": result.symbol,
                "strategy": result.strategy,
                "total_ticks": result.total_ticks,
                "duration_seconds": result.duration_seconds,
            },
            "summary": {
                "total_trades": result.total_trades,
                "wins": result.wins,
                "losses": result.losses,
                "win_rate": result.win_rate,
                "net_pnl": result.net_pnl,
                "profit_factor": result.profit_factor,
                "sharpe_ratio": result.sharpe_ratio,
                "max_drawdown": result.max_drawdown,
            },
            "trades": [asdict(t) for t in result.trades],
            "equity_curve": result.equity_curve,
        }
        path.write_text(json.dumps(data, indent=2, default=str))
        LOG.info("📄  Exported result to %s", path)

    # ── Multi-symbol ──

    async def run_multi(
        self,
        symbols: list[str],
        strategy: str = "default",
        count_per_symbol: int = 500,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, BacktestResult]:
        """Run backtest across multiple symbols.

        Returns:
            Dict mapping symbol -> BacktestResult.
        """
        results: dict[str, BacktestResult] = {}
        for sym in symbols:
            result = await self.run(
                symbol=sym,
                strategy=strategy,
                count=count_per_symbol,
                start_date=start_date,
                end_date=end_date,
            )
            results[sym] = result
        return results

    # ── Internal ──

    def _reset(self) -> None:
        self._trades.clear()
        self._equity.clear()
        self._equity.append(self._initial_balance)
        self._cycle_id = 0
        self._consecutive_wins = 0
        self._consecutive_losses = 0
        self._balance = self._initial_balance
        self._peak_balance = self._initial_balance

    async def _fetch_ticks(self, symbol: str, count: int) -> list[Any]:
        if self._tick_provider is None:
            LOG.warning("No tick provider — using empty list")
            return []

        if hasattr(self._tick_provider, "get_ticks"):
            if hasattr(self._tick_provider.get_ticks, "__await__"):
                return await self._tick_provider.get_ticks(symbol, count=count)
            return self._tick_provider.get_ticks(symbol, count=count)

        if hasattr(self._tick_provider, "get_ticks_history"):
            if hasattr(self._tick_provider.get_ticks_history, "__await__"):
                return await self._tick_provider.get_ticks_history(symbol, count=count)
            return self._tick_provider.get_ticks_history(symbol, count=count)

        return []

    @staticmethod
    def _filter_by_date(
        ticks: list[Any],
        start_date: str | None,
        end_date: str | None,
    ) -> list[Any]:
        if not start_date and not end_date:
            return ticks

        start_ts = (
            datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=UTC).timestamp()
            if start_date else 0
        )
        end_ts = (
            datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=UTC).timestamp()
            if end_date else float("inf")
        )

        filtered: list[Any] = []
        for tick in ticks:
            epoch = getattr(tick, "epoch", 0)
            if start_ts <= epoch <= end_ts:
                filtered.append(tick)
        return filtered

    async def _evaluate_strategy(self, tick, index: int, all_ticks: list) -> dict | None:
        fn = self._strategy_fn
        if fn is None:
            return None
        assert fn is not None  # Narrowed above, for type checker
        try:
            result = fn(tick, index, all_ticks, self._balance)
            if hasattr(result, "__await__"):
                result = await result
            return result
        except Exception as exc:
            LOG.debug("Strategy evaluation error at tick %d: %s", index, exc)
            return None

    def _execute_trade(self, tick, signal: dict, symbol: str, strategy: str) -> None:
        self._cycle_id += 1
        action = signal.get("action", "CALL")
        stake = float(signal.get("stake", settings.BROKER_DEFAULT_STAKE))
        price = float(getattr(tick, "price", getattr(tick, "close", 0)))

        # Outcome determined by strategy or next tick
        outcome = signal.get("outcome", "WIN")
        pnl = float(signal.get("pnl", stake)) if outcome == "WIN" else -stake

        trade = BacktestTrade(
            tick_time=datetime.fromtimestamp(
                getattr(tick, "epoch", time.time()), tz=UTC
            ).isoformat(),
            symbol=symbol,
            action=action,
            price=price,
            stake=stake,
            outcome=outcome,
            pnl=pnl,
            strategy=strategy,
            cycle_id=self._cycle_id,
        )
        self._trades.append(trade)

        self._balance += pnl
        if self._balance > self._peak_balance:
            self._peak_balance = self._balance

        if outcome == "WIN":
            self._consecutive_wins += 1
            self._consecutive_losses = 0
        else:
            self._consecutive_losses += 1
            self._consecutive_wins = 0

    def _update_equity(self) -> None:
        self._equity.append(self._balance)

    def _aggregate(
        self, symbol: str, strategy: str, duration: float,
    ) -> BacktestResult:
        if not self._trades:
            return BacktestResult(
                symbol=symbol,
                strategy=strategy,
                duration_seconds=duration,
                equity_curve=self._equity,
            )

        wins = [t for t in self._trades if t.outcome == "WIN"]
        losses = [t for t in self._trades if t.outcome == "LOSS"]

        total = len(self._trades)
        total_wins = len(wins)
        total_losses = len(losses)
        win_rate = total_wins / total if total > 0 else 0.0

        gross_profit = sum(t.pnl for t in wins)
        gross_loss = abs(sum(t.pnl for t in losses))
        net_pnl = gross_profit - gross_loss

        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")

        avg_win = gross_profit / total_wins if total_wins > 0 else 0.0
        avg_loss = gross_loss / total_losses if total_losses > 0 else 0.0

        # Sharpe ratio
        returns = [t.pnl / self._initial_balance for t in self._trades]
        sharpe = self._compute_sharpe(returns)

        # Max drawdown
        max_dd = self._compute_max_drawdown()

        return BacktestResult(
            symbol=symbol,
            strategy=strategy,
            total_ticks=0,
            total_trades=total,
            wins=total_wins,
            losses=total_losses,
            win_rate=round(win_rate, 4),
            gross_profit=round(gross_profit, 2),
            gross_loss=round(gross_loss, 2),
            net_pnl=round(net_pnl, 2),
            profit_factor=round(profit_factor, 4),
            sharpe_ratio=round(sharpe, 4),
            max_drawdown=round(max_dd, 4),
            avg_win=round(avg_win, 4),
            avg_loss=round(avg_loss, 4),
            max_consecutive_wins=self._consecutive_wins,
            max_consecutive_losses=self._consecutive_losses,
            duration_seconds=round(duration, 2),
            trades=list(self._trades),
            equity_curve=list(self._equity),
        )

    @staticmethod
    def _compute_sharpe(returns: list[float], risk_free_rate: float = 0.02) -> float:
        if len(returns) < 2:
            return 0.0
        mean_ret = sum(returns) / len(returns)
        variance = sum((r - mean_ret) ** 2 for r in returns) / (len(returns) - 1)
        if variance <= 0:
            return 0.0
        std = math.sqrt(variance)
        if std == 0:
            return 0.0
        excess = mean_ret - (risk_free_rate / len(returns))
        return excess / std * math.sqrt(len(returns))

    def _compute_max_drawdown(self) -> float:
        peak = float("-inf")
        max_dd = 0.0
        for eq in self._equity:
            if eq > peak:
                peak = eq
            dd = (peak - eq) / peak if peak > 0 else 0.0
            if dd > max_dd:
                max_dd = dd
        return max_dd


__all__ = [
    "BacktestEngine",
    "BacktestResult",
    "BacktestTrade",
]
