"""Tests for trading_bot.config — configuration loading and helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trading_bot.config import (
    BotConfig,
    _candles_from_value,
    _load_json,
    _load_toml,
    _load_yaml,
    _risk_from_value,
    load_config,
    load_config_from_env,
    merge_configs,
)
from trading_bot.engine.risk import RiskConfig
from trading_bot.providers.base import Candle


class TestRiskFromValue:
    """RiskConfig parsing helper."""

    def test_dict(self) -> None:
        value = {
            "max_risk_per_trade_pct": 1.5,
            "max_drawdown_pct": 5.0,
            "max_open_positions": 2,
        }
        cfg = _risk_from_value(value)
        assert isinstance(cfg, RiskConfig)
        assert cfg.max_risk_per_trade_pct == 1.5

    def test_instance(self) -> None:
        original = RiskConfig(max_risk_per_trade_pct=3.0)
        cfg = _risk_from_value(original)
        assert cfg is original
        assert cfg.max_risk_per_trade_pct == 3.0

    def test_invalid_type(self) -> None:
        with pytest.raises(TypeError):
            _risk_from_value(123)


class TestCandlesFromValue:
    """Candle list parsing helper."""

    def test_none_returns_none(self) -> None:
        assert _candles_from_value(None) is None

    def test_candle_instances(self) -> None:
        c1 = Candle(symbol="X", timeframe="1h", open=1, high=2, low=0, close=1, volume=10)
        c2 = Candle(symbol="Y", timeframe="1h", open=2, high=3, low=1, close=2, volume=20)
        result = _candles_from_value([c1, c2])
        assert result is not None
        assert len(result) == 2
        assert result[0].symbol == "X"

    def test_dict_items(self) -> None:
        raw = [
            {
                "symbol": "XAU/USD",
                "timeframe": "1h",
                "open": 2500.0,
                "high": 2510.0,
                "low": 2490.0,
                "close": 2505.0,
                "volume": 1000.0,
            },
        ]
        result = _candles_from_value(raw)
        assert result is not None
        assert result[0].symbol == "XAU/USD"

    def test_invalid_item(self) -> None:
        with pytest.raises(TypeError):
            _candles_from_value([123])

    def test_candles_from_value_invalid_type(self) -> None:
        with pytest.raises(TypeError):
            _candles_from_value(123)


class TestBotConfig:
    """BotConfig dataclass validation."""

    def test_minimum_valid(self) -> None:
        cfg = BotConfig(symbols=["XAU/USD"], risk=RiskConfig(), strategies=[{"name": "grid"}])
        assert cfg.symbols == ["XAU/USD"]
        assert cfg.provider == "paper"

    def test_symbols_empty(self) -> None:
        with pytest.raises(ValueError, match="non-empty list"):
            BotConfig(symbols=[], risk=RiskConfig(), strategies=[{"name": "grid"}])

    def test_symbols_non_string(self) -> None:
        with pytest.raises(ValueError, match="all symbols must be strings"):
            BotConfig(symbols=[123], risk=RiskConfig(), strategies=[{"name": "grid"}])

    def test_risk_wrong_type(self) -> None:
        with pytest.raises(TypeError, match="RiskConfig"):
            BotConfig(symbols=["X"], risk={}, strategies=[{"name": "grid"}])

    def test_strategies_wrong_type(self) -> None:
        with pytest.raises(ValueError, match="list of dicts"):
            BotConfig(symbols=["X"], risk=RiskConfig(), strategies=["grid"])


class TestLoadConfigJson:
    """load_config with JSON files."""

    def test_load_json(self, tmp_path: Path) -> None:
        path = tmp_path / "config.json"
        path.write_text(json.dumps({
            "symbols": ["XAU/USD"],
            "risk": {"max_risk_per_trade_pct": 2.0},
            "strategies": [{"name": "grid", "levels": 5}],
            "provider": "paper",
            "cycle_interval_seconds": 30,
        }))
        cfg = load_config(path)
        assert cfg.symbols == ["XAU/USD"]
        assert cfg.risk.max_risk_per_trade_pct == 2.0
        assert cfg.strategies == [{"name": "grid", "levels": 5}]
        assert cfg.provider == "paper"
        assert cfg.cycle_interval_seconds == 30

    def test_load_json_not_object(self, tmp_path: Path) -> None:
        path = tmp_path / "config.json"
        path.write_text("[]")
        with pytest.raises(ValueError, match="top-level object"):
            _load_json(path)

    def test_load_toml(self, tmp_path: Path) -> None:
        path = tmp_path / "config.toml"
        path.write_text(
            'symbols = ["BTC/USD"]\n'
            'timeframe = "15m"\n'
            'provider = "ccxt"\n'
            'cycle_interval_seconds = 120\n'
            '\n'
            '[risk]\n'
            'max_risk_per_trade_pct = 1.5\n'
            '\n'
            '[[strategies]]\n'
            'name = "trend"\n'
            'fast_period = 10\n'
        )
        cfg = load_config(path)
        assert cfg.symbols == ["BTC/USD"]
        assert cfg.risk.max_risk_per_trade_pct == 1.5

    def test_load_toml_not_table(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import tomllib
        path = tmp_path / "config.toml"
        path.write_text("answer = 42\n")
        monkeypatch.setattr(tomllib, "load", lambda _handle: 42)
        with pytest.raises(ValueError, match="top-level table"):
            _load_toml(path)

    def test_unsupported_extension(self, tmp_path: Path) -> None:
        path = tmp_path / "config.txt"
        path.write_text("{}")
        with pytest.raises(ValueError, match="Unsupported"):
            load_config(path)


class TestLoadConfigFromEnv:
    """Environment variable config loader."""

    def test_load_config_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRADE_BOT_SYMBOLS", "[\"XAU/USD\", \"EUR/USD\"]")
        monkeypatch.setenv("TRADE_BOT_PROVIDER", "paper")
        monkeypatch.setenv("TRADE_BOT_CYCLE_INTERVAL_SECONDS", "45")
        cfg = load_config_from_env()
        assert cfg["symbols"] == ["XAU/USD", "EUR/USD"]
        assert cfg["provider"] == "paper"
        assert cfg["cycle_interval_seconds"] == 45
        assert "other_key" not in cfg


class TestMergeConfigs:
    """Deep merge helper."""

    def test_merge_nested(self) -> None:
        base = {
            "symbols": ["XAU/USD"],
            "risk": {"max_risk_per_trade_pct": 1.0},
            "provider": "paper",
        }
        override = {
            "risk": {"max_daily_risk_pct": 3.0},
            "provider": "ccxt",
        }
        merged = merge_configs(base, override)
        assert merged["risk"]["max_risk_per_trade_pct"] == 1.0
        assert merged["risk"]["max_daily_risk_pct"] == 3.0
        assert merged["provider"] == "ccxt"


class TestYamlLoader:
    """YAML config parsing."""

    def test_load_yaml(self, tmp_path: Path) -> None:
        pytest.importorskip("yaml")
        path = tmp_path / "config.yaml"
        path.write_text(
            "symbols:\n"
            "  - XAU/USD\n"
            "risk:\n"
            "  max_risk_per_trade_pct: 2.0\n"
            "strategies:\n"
            "  - name: grid\n"
            "    levels: 5\n"
        )
        cfg = load_config(path)
        assert cfg.symbols == ["XAU/USD"]
        assert cfg.risk.max_risk_per_trade_pct == 2.0
        assert cfg.strategies[0]["name"] == "grid"

    def test_load_yaml_not_mapping(self, tmp_path: Path) -> None:
        pytest.importorskip("yaml")
        path = tmp_path / "config.yaml"
        path.write_text("- a\n- b\n")
        with pytest.raises(ValueError, match="top-level mapping"):
            _load_yaml(path)

    def test_yaml_without_pyyaml_raises_helpful_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        path = tmp_path / "config.yaml"
        path.write_text("symbols: [XAU/USD]\n")

        # Simulate PyYAML being absent.
        import builtins
        real_import = builtins.__import__

        def _fake_import(name: str, *args: object, **kwargs: object) -> object:
            if name == "yaml":
                raise ImportError("No module named 'yaml'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _fake_import)
        with pytest.raises(ImportError, match="PyYAML"):
            load_config(path)
