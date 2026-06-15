"""Command-line entry point for the trading bot.

Provides ``run``, ``backtest``, and ``status`` subcommands.  The ``run``
command wires together the configured provider, strategies, orchestrator and
executor and executes trading cycles until interrupted.
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from pathlib import Path
from typing import Any

from trading_bot.config import BotConfig, load_config
from trading_bot.engine import (
    ORDER_PLACED,
    EventBus,
    PortfolioTracker,
    RiskManager,
    SignalExecutor,
    TradingOrchestrator,
)
from trading_bot.persistence import PersistenceStore
from trading_bot.providers.paper.paper_trader import PaperTradingProvider
from trading_bot.providers.registry import ProviderRegistry
from trading_bot.strategies.base import BaseStrategy
from trading_bot.strategies.grid import GridStrategy
from trading_bot.strategies.trend import TrendStrategy

# ---------------------------------------------------------------------------
#  Strategy factory
# ---------------------------------------------------------------------------

_STRATEGY_TYPES: dict[str, type[BaseStrategy]] = {
    "grid": GridStrategy,
    "trend": TrendStrategy,
}


def _build_strategy(name: str, provider: Any, params: dict[str, Any]) -> BaseStrategy:
    """Instantiate a named strategy with the given parameters."""
    strategy_cls = _STRATEGY_TYPES.get(name)
    if strategy_cls is None:
        raise ValueError(f"Unknown strategy '{name}'. Supported: {list(_STRATEGY_TYPES)}")
    return strategy_cls(provider=provider, params=params)


# ---------------------------------------------------------------------------
#  Argument parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="trading-bot",
        description="Unified multi-market trading bot",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config.yaml"),
        help="Path to configuration file (default: config.yaml)",
    )

    sub = parser.add_subparsers(dest="command", required=False)

    run = sub.add_parser("run", help="Run the live trading loop")
    run.add_argument(
        "--db",
        type=Path,
        default=Path("trading_bot.db"),
        help="Path to SQLite database (default: trading_bot.db)",
    )

    backtest = sub.add_parser("backtest", help="Run a backtest")
    backtest.add_argument("--from", dest="from_date", help="Start date (YYYY-MM-DD)")
    backtest.add_argument("--to", dest="to_date", help="End date (YYYY-MM-DD)")

    status = sub.add_parser("status", help="Show stored status / history")
    status.add_argument(
        "--db",
        type=Path,
        default=Path("trading_bot.db"),
        help="Path to SQLite database (default: trading_bot.db)",
    )

    return parser


# ---------------------------------------------------------------------------
#  Command handlers
# ---------------------------------------------------------------------------


def _signal_handler(shutdown: asyncio.Event) -> None:
    """Request a graceful shutdown when a signal is received."""
    shutdown.set()


async def _cmd_run(config: BotConfig, db_path: Path) -> int:
    """Run the bot loop until interrupted."""
    registry = ProviderRegistry()
    provider: Any = registry.get(config.provider)

    # Fall back to paper provider if requested provider is not registered.
    if provider is None:
        provider = PaperTradingProvider(
            initial_balance=config.initial_balance,
        )

    # Seed paper provider with optional candles.
    if isinstance(provider, PaperTradingProvider) and config.paper_candles is not None:
        for candle in config.paper_candles:
            provider.inject_candles(candle.symbol, [candle])

    event_bus = EventBus()
    portfolio = PortfolioTracker(initial_balance=config.initial_balance)
    risk_manager = RiskManager(config.risk)
    executor = SignalExecutor(
        provider=provider,
        risk_manager=risk_manager,
        portfolio=portfolio,
        event_bus=event_bus,
    )
    orchestrator = TradingOrchestrator(
        event_bus=event_bus,
        risk_manager=risk_manager,
        portfolio=portfolio,
    )

    for strategy_cfg in config.strategies:
        name = strategy_cfg.get("name")
        if not isinstance(name, str):
            raise ValueError("strategy entry must include a 'name' string")
        params = {k: v for k, v in strategy_cfg.items() if k != "name"}
        strategy = _build_strategy(name, provider, params)
        orchestrator.register_strategy(strategy)

    store = PersistenceStore(db_path)
    await store.connect()

    # Persist lifecycle events.
    async def _on_order(event: Any) -> None:
        # Orders are persisted by the executor; this hook could be extended.
        pass

    event_bus.subscribe(ORDER_PLACED, _on_order)

    await executor.start()
    await orchestrator.start()

    shutdown = asyncio.Event()

    for sig in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(sig, _signal_handler, shutdown)

    try:
        while not shutdown.is_set():
            for symbol in config.symbols:
                await orchestrator.run_cycle(symbol, config.timeframe)
                await asyncio.sleep(0)  # allow executor to process signals
            await asyncio.wait_for(shutdown.wait(), timeout=config.cycle_interval_seconds)
    except TimeoutError:
        pass
    finally:
        await orchestrator.stop()
        await executor.stop()
        await store.close()

    return 0


async def _cmd_backtest(_config: BotConfig, args: argparse.Namespace) -> int:
    """Backtest placeholder."""
    print(f"backtest command is not yet implemented (from={args.from_date}, to={args.to_date})")
    return 0


async def _cmd_status(db_path: Path) -> int:
    """Show a summary of the stored database."""
    store = PersistenceStore(db_path)
    await store.connect()
    try:
        signals = await store.get_signals(limit=5)
        orders = await store.get_orders(limit=5)
        positions = await store.get_positions(status="open")
        print(f"Database: {db_path}")
        print(f"  Signals: {len(signals)} recent")
        print(f"  Orders: {len(orders)} recent")
        print(f"  Open positions: {len(positions)}")
    finally:
        await store.close()
    return 0


# ---------------------------------------------------------------------------
#  Public entry point
# ---------------------------------------------------------------------------


async def main(argv: list[str] | None = None) -> int:
    """Async entry point; returns a shell exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "run":
        config = load_config(args.config)
        return await _cmd_run(config, args.db)
    if args.command == "backtest":
        config = load_config(args.config)
        return await _cmd_backtest(config, args)
    if args.command == "status":
        return await _cmd_status(args.db)
    parser.print_help()
    return 1


if __name__ == "__main__":  # pragma: no cover - exercised by CLI invocation
    sys.exit(asyncio.run(main()))
