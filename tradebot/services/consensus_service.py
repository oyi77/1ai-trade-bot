"""Consensus engine service — wraps scripts/engine_consensus for clean import.

Temporary proxy until scripts/engine_consensus is absorbed into
tradebot.engines.consensus. scripts is a PEP 420 implicit namespace
package, so imports work at runtime.

Provides TTL-based caching so multiple callers requesting the same
symbol within the cache window share a single engine run.
"""

from __future__ import annotations

import logging
from typing import Any

from scripts.engine_consensus import (  # type: ignore[import-not-found]
    TF_WEIGHTS as _TF_WEIGHTS,
)
from scripts.engine_consensus import (
    TIMEFRAMES as _TIMEFRAMES,
)
from scripts.engine_consensus import (
    run_engine_consensus as _run,
)
from tradebot.storage.cache import TieredCache

LOG = logging.getLogger(__name__)

_signal_cache = TieredCache(default_ttl=120)


def run_engine_consensus(
    ohlcv: list[dict] | None = None,
    price: float | None = None,
    symbol: str = "XAUUSD",
) -> dict[str, Any]:
    cache_key = f"signal:{symbol}"
    cached = _signal_cache.get(cache_key)
    if isinstance(cached, dict):
        LOG.debug("Signal cache HIT for %s", cache_key)
        return cached

    LOG.debug("Signal cache MISS for %s — running engine consensus", cache_key)
    result = _run(ohlcv=ohlcv, price=price, symbol=symbol)
    if result:
        _signal_cache.set(cache_key, result)
    return result


def get_tf_weights() -> dict[str, float]:
    return dict(_TF_WEIGHTS)


def get_timeframes() -> list[str]:
    return list(_TIMEFRAMES)


__all__ = ["get_timeframes", "get_tf_weights", "run_engine_consensus"]
