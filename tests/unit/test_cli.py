"""Tests for trading_bot.cli — command-line interface."""

from __future__ import annotations

import json
from pathlib import Path

from trading_bot.cli import build_parser, main


class TestBuildParser:
    """Argument parser construction."""

    def test_run_subcommand(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["run"])
        assert args.command == "run"
        assert args.config == Path("config.yaml")
        assert args.db == Path("trading_bot.db")

    def test_run_with_config(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--config", "bot.yaml", "run"])
        assert args.command == "run"
        assert args.config == Path("bot.yaml")

    def test_backtest_subcommand(self) -> None:
        parser = build_parser()
        args = parser.parse_args([
            "backtest",
            "--from", "2025-01-01",
            "--to", "2025-02-01",
        ])
        assert args.command == "backtest"
        assert args.from_date == "2025-01-01"
        assert args.to_date == "2025-02-01"

    def test_status_subcommand(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["status", "--db", "other.db"])
        assert args.command == "status"
        assert args.db == Path("other.db")


class TestMainBacktest:
    """Backtest and status commands."""

    async def test_backtest_not_implemented(self, tmp_path: Path) -> None:
        config_path = tmp_path / "cfg.json"
        config_path.write_text(json.dumps({
            "symbols": ["XAU/USD"],
            "risk": {},
            "strategies": [],
        }))
        code = await main(["--config", str(config_path), "backtest", "--from", "2025-01-01"])
        assert code == 0

    async def test_status_empty_db(self, tmp_path: Path) -> None:
        db_path = tmp_path / "empty.db"
        code = await main(["status", "--db", str(db_path)])
        assert code == 0


class TestMainRun:
    """Run command wires components and loops until interrupted."""

    async def test_run_with_paper_provider(self, tmp_path: Path) -> None:
        config_path = tmp_path / "cfg.json"
        config_path.write_text(json.dumps({
            "symbols": ["XAU/USD"],
            "risk": {"max_risk_per_trade_pct": 1.0},
            "strategies": [{"name": "trend", "fast_period": 5, "slow_period": 10}],
            "provider": "paper",
            "cycle_interval_seconds": 0,
            "paper_candles": [
                {
                    "symbol": "XAU/USD",
                    "timeframe": "1h",
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 100.0 + i,
                    "volume": 1000.0,
                }
                for i in range(20)
            ],
        }))

        db_path = tmp_path / "run.db"

        # Use a tiny cycle interval and let it time out once.
        code = await main(["--config", str(config_path), "run", "--db", str(db_path)])
        assert code == 0
        assert db_path.exists()
