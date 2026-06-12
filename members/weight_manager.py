"""
weight_manager.py — Hot-Reload Dynamic Scoring Weights.

Reads learning_weights.json from pattern_extractor.py output,
applies learned per-regime weights to raw engine scores, with
graceful fallback to defaults when data is missing or corrupt.

Thread-safe: reads JSON file on every call (hot-reload),
no in-memory caching needed at this scale.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

logger = logging.getLogger("hermes.weight-manager")

WIB = timezone(timedelta(hours=7))

# ── Fallback defaults (used when JSON is missing / corrupt / regime unknown) ──
DEFAULT_WEIGHTS = {
    "smc": 0.40,
    "liq": 0.30,
    "macro": 0.30,
}

# ── Default per-regime if JSON is completely absent ──
REGIME_DEFAULTS = {
    "trending":  {"smc": 0.50, "liq": 0.20, "macro": 0.30},
    "ranging":   {"smc": 0.30, "liq": 0.45, "macro": 0.25},
    "volatile":  {"smc": 0.20, "liq": 0.30, "macro": 0.50},
    "unknown":   {"smc": 0.40, "liq": 0.30, "macro": 0.30},
}

# ── Default path relative to project root ──
_SCRIPT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_JSON_PATH = _SCRIPT_DIR / "data" / "vilona_tradefx" / "learning_weights.json"


def _load_weights_json(path: str) -> dict | None:
    """Load and parse learning_weights.json. Returns None on any failure."""
    try:
        raw = Path(path).read_text(encoding="utf-8")
        data = json.loads(raw)
    except FileNotFoundError:
        logger.debug("learning_weights.json not found at %s", path)
        return None
    except json.JSONDecodeError as exc:
        logger.warning("learning_weights.json is corrupt: %s", exc)
        return None
    except OSError as exc:
        logger.warning("Cannot read learning_weights.json: %s", exc)
        return None

    # Extract suggested_weights section
    weights = data.get("suggested_weights", {}) if isinstance(data, dict) else {}
    if not weights:
        return None
    return weights


def get_current_weights(
    market_regime: str = "",
    json_path: str | None = None,
) -> dict[str, float]:
    """Hot-reload per-regime scoring weights from learning_weights.json.

    Args:
        market_regime: One of 'trending', 'ranging', 'volatile', 'unknown'.
                       Blank/empty → uses 'unknown' fallback.
        json_path: Override path to learning_weights.json.
                   Default: data/vilona_tradefx/learning_weights.json

    Returns:
        {'smc': weight, 'liq': weight, 'macro': weight}
        Always returns a valid dict — never raises.

    Fallback chain:
        1. Exact match in learning_weights.json for this regime
        2. 'unknown' key in learning_weights.json
        3. REGIME_DEFAULTS for this regime
        4. DEFAULT_WEIGHTS (hardcoded floor)
    """
    path = json_path or str(DEFAULT_JSON_PATH)
    regime = market_regime.strip().lower() if market_regime else "unknown"

    # ── Attempt hot-reload from JSON ──
    weights_data = _load_weights_json(path)

    if weights_data:
        # Try exact regime match
        if regime in weights_data:
            w = weights_data[regime]
            if isinstance(w, dict) and all(k in w for k in ("smc", "liq", "macro")):
                return {
                    "smc": float(w["smc"]),
                    "liq": float(w["liq"]),
                    "macro": float(w["macro"]),
                }
        # Try 'unknown' fallback from JSON
        if "unknown" in weights_data:
            w = weights_data["unknown"]
            if isinstance(w, dict) and all(k in w for k in ("smc", "liq", "macro")):
                return {
                    "smc": float(w["smc"]),
                    "liq": float(w["liq"]),
                    "macro": float(w["macro"]),
                }

    # ── JSON unavailable or regime missing → regime defaults ──
    if regime in REGIME_DEFAULTS:
        return dict(REGIME_DEFAULTS[regime])

    return dict(DEFAULT_WEIGHTS)


def apply_weights(
    score_smc: float,
    score_liquidity: float,
    score_macro: float,
    regime: str = "unknown",
) -> float:
    """Compute weighted total score from raw component scores.

    Args:
        score_smc, score_liquidity, score_macro: Raw 0-1 engine scores
        regime: Market regime name for weight lookup

    Returns:
        Weighted score (0.0 – 1.0)
    """
    w = get_current_weights(regime)
    total = (
        score_smc * w["smc"]
        + score_liquidity * w["liq"]
        + score_macro * w["macro"]
    )
    return round(total, 4)


def get_weights_summary(json_path: str | None = None) -> dict:
    """Return all loaded weights + metadata for reporting.

    Returns:
        {'regimes': {regime: weights_dict}, 'source': 'json'|'defaults',
         'generated_at': str, 'lookback_days': int}
    """
    path = json_path or str(DEFAULT_JSON_PATH)
    weights_data = _load_weights_json(path)

    if weights_data and "_empty" not in weights_data:
        # Read metadata from the full JSON if available
        try:
            raw = json.loads(Path(path).read_text())
            generated_at = raw.get("generated_at", "")
            lookback = raw.get("lookback_days", 0)
        except Exception:
            generated_at = ""
            lookback = 0

        return {
            "regimes": {k: v for k, v in weights_data.items() if isinstance(v, dict)},
            "source": "json",
            "generated_at": generated_at,
            "lookback_days": lookback,
        }

    # Fallback to defaults
    return {
        "regimes": dict(REGIME_DEFAULTS),
        "source": "defaults",
        "generated_at": "",
        "lookback_days": 0,
    }


def last_updated(json_path: str | None = None) -> str:
    """Return ISO timestamp of when learning_weights.json was last modified."""
    path = json_path or str(DEFAULT_JSON_PATH)
    try:
        mtime = os.path.getmtime(path)
        dt = datetime.fromtimestamp(mtime, tz=WIB)
        return dt.isoformat()
    except OSError:
        return "never"
