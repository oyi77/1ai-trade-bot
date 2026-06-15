"""Trading bot configuration loading and validation.

Supports JSON, YAML, and TOML config files; environment-variable overrides;
and a deep-merge helper for layered configuration.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from trading_bot.engine.risk import RiskConfig
from trading_bot.providers.base import Candle


def _risk_from_value(value: Any) -> RiskConfig:
    """Parse a ``RiskConfig`` from a dict or existing instance."""
    if isinstance(value, RiskConfig):
        return value
    if isinstance(value, dict):
        return RiskConfig(**value)
    raise TypeError(
        f"risk configuration must be a mapping or RiskConfig, got {type(value).__name__}"
    )


def _candles_from_value(value: Any) -> list[Candle] | None:
    """Parse a list of ``Candle`` instances from a sequence of dicts/instances."""
    if value is None:
        return None
    if isinstance(value, list):
        candles: list[Candle] = []
        for item in value:
            if isinstance(item, Candle):
                candles.append(item)
            elif isinstance(item, dict):
                candles.append(Candle(**item))
            else:
                raise TypeError(
                    f"paper_candles item must be a Candle or dict, got {type(item).__name__}"
                )
        return candles
    raise TypeError(
        f"paper_candles must be a list of Candle entries or None, got {type(value).__name__}"
    )


@dataclass
class BotConfig:
    """Complete runtime configuration for the trading bot.

    Attributes:
        initial_balance: Starting account balance for the bot session.
        risk: Risk-management parameters (``RiskConfig``).
        symbols: Non-empty list of symbols to trade.
        timeframe: Candle timeframe used by strategies (e.g. ``1h``, ``15m``).
        provider: Provider backend identifier (e.g. ``paper``, ``ccxt``).
        strategies: List of strategy configuration dicts.
        cycle_interval_seconds: Seconds between bot execution cycles.
        paper_candles: Optional seed candle data for the paper provider.
    """

    symbols: list[str]
    risk: RiskConfig
    strategies: list[dict[str, Any]]
    initial_balance: float = 10_000.0
    timeframe: str = "1h"
    provider: str = "paper"
    cycle_interval_seconds: int = 60
    paper_candles: list[Candle] | None = None

    def __post_init__(self) -> None:
        """Validate required fields after construction."""
        if not isinstance(self.symbols, list) or not self.symbols:
            raise ValueError("symbols must be a non-empty list")
        if any(not isinstance(symbol, str) for symbol in self.symbols):
            raise ValueError("all symbols must be strings")
        if not isinstance(self.risk, RiskConfig):
            raise TypeError("risk must be a RiskConfig instance")
        if not isinstance(self.strategies, list) or any(
            not isinstance(strategy, dict) for strategy in self.strategies
        ):
            raise ValueError("strategies must be a list of dicts")


# ---------------------------------------------------------------------------
#  File loaders
# ---------------------------------------------------------------------------


def _load_json(path: Path) -> dict[str, Any]:
    """Load a JSON configuration file."""
    with path.open("r", encoding="utf-8") as handle:
        data: Any = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"JSON config must contain a top-level object: {path}")
    return data


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML configuration file; PyYAML is required."""
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - exercised when absent
        raise ImportError(
            "YAML config parsing requires PyYAML. Install it with: pip install pyyaml"
        ) from exc

    with path.open("r", encoding="utf-8") as handle:
        data: Any = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"YAML config must contain a top-level mapping: {path}")
    return data


def _load_toml(path: Path) -> dict[str, Any]:
    """Load a TOML configuration file using the stdlib ``tomllib`` module."""
    import tomllib

    with path.open("rb") as handle:
        data: Any = tomllib.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"TOML config must contain a top-level table: {path}")
    return data


def load_config(path: str | Path) -> BotConfig:
    """Load a ``BotConfig`` from a JSON, YAML, or TOML file.

    The file format is inferred from its extension. YAML parsing requires
    PyYAML to be installed; otherwise an ``ImportError`` is raised with a
    helpful installation message.
    """
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".json":
        raw = _load_json(path)
    elif suffix in {".yaml", ".yml"}:
        raw = _load_yaml(path)
    elif suffix == ".toml":
        raw = _load_toml(path)
    else:
        raise ValueError(
            f"Unsupported config extension '{suffix}'. Use .json, .yaml, .yml, or .toml."
        )

    risk = _risk_from_value(raw.pop("risk", {}))
    paper_candles = _candles_from_value(raw.pop("paper_candles", None))

    return BotConfig(
        risk=risk,
        paper_candles=paper_candles,
        **raw,
    )


# ---------------------------------------------------------------------------
#  Environment variable loader
# ---------------------------------------------------------------------------


def _parse_env_value(value: str) -> Any:
    """Attempt to parse an environment variable value.

    Order: JSON literal, integer, float, then plain string.
    """
    try:
        return json.loads(value)
    except (json.JSONDecodeError, ValueError):
        pass
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


def load_config_from_env(prefix: str = "TRADE_BOT_") -> dict[str, Any]:
    """Build a configuration dict from environment variables.

    Captures variables whose names start with ``prefix``, strips the prefix,
    lowercases the key, and tries to interpret the value as JSON (for lists
    and dicts) or as a number before falling back to the raw string.
    """
    result: dict[str, Any] = {}
    for key, value in os.environ.items():
        if not key.startswith(prefix):
            continue
        config_key = key[len(prefix) :].lower()
        result[config_key] = _parse_env_value(value)
    return result


# ---------------------------------------------------------------------------
#  Config merging
# ---------------------------------------------------------------------------


def merge_configs(
    base: dict[str, Any],
    override: dict[str, Any],
) -> dict[str, Any]:
    """Deep-merge ``override`` into ``base``.

    Nested dictionaries are merged recursively; lists and all other values are
    replaced entirely by the override value.
    """
    merged: dict[str, Any] = dict(base)
    for key, override_value in override.items():
        if (
            key in merged
            and isinstance(merged[key], dict)
            and isinstance(override_value, dict)
        ):
            merged[key] = merge_configs(merged[key], override_value)
        else:
            merged[key] = override_value
    return merged


