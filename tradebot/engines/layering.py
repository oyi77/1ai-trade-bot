"""
layering.py — Smart Layering Signal Generator Engine

Migrated from: scripts/layering.py
Conforms to: tradebot.engines.base.Engine interface

Generates multi-layer entries from a single signal.
Layer 1: 40% risk at current price, standard TP
Layer 2: 30% risk at +LayerSpacing pips from price, wider TP
Layer 3: 30% risk at +2x LayerSpacing, widest TP
All layers share the same SL.
"""

from __future__ import annotations

import logging

from tradebot.config.settings import settings
from tradebot.engines.base import Engine
from tradebot.exceptions import SignalError
from tradebot.models import Signal, SignalGrade, SignalSource, Tick

LOG = logging.getLogger(__name__)


def _pip_value(symbol: str = "XAUUSD") -> float:
    s = symbol.upper()
    if s in ("XAUUSD", "GOLD"):
        return 0.1
    if s in ("BTCUSD", "BTC"):
        return 1.0
    if s.endswith("JPY"):
        return 0.01
    if s in ("USOIL", "OIL", "CL"):
        return 0.01
    return 0.0001


def generate_layers(
    action: str, entry: float, sl: float, tp: float,
    symbol: str = "XAUUSD",
    layer_count: int = 3, layer_spacing_pips: float = 50.0,
    risk_split: list[float] | None = None,
    tp_pips: list[float] | None = None,
) -> list[dict]:
    """Generate layered entries from a single signal.

    Args:
        action: "BUY" or "SELL"
        entry: Primary entry price
        sl: Stop loss (shared across all layers)
        tp: Primary take profit
        symbol: Trading symbol
        layer_count: Number of layers (1-5)
        layer_spacing_pips: Spacing between layers in pips
        risk_split: Risk percentage per layer (e.g. [0.4, 0.3, 0.3])
        tp_pips: Custom TP pips per layer

    Returns:
        List of dicts with entry, tp, risk_pct per layer
    """
    if risk_split is None:
        if layer_count == 1:
            risk_split = [1.0]
        elif layer_count == 2:
            risk_split = [0.5, 0.5]
        elif layer_count == 3:
            risk_split = [0.4, 0.3, 0.3]
        elif layer_count == 4:
            risk_split = [0.35, 0.25, 0.25, 0.15]
        else:
            risk_split = [0.3, 0.2, 0.2, 0.15, 0.15]

    if tp_pips is None:
        tp_pips = [(i + 1) * layer_spacing_pips * 2 for i in range(layer_count)]

    pip_value = _pip_value(symbol)
    layers: list[dict] = []
    is_buy = action.upper() == "BUY"

    for i in range(layer_count):
        offset = i * layer_spacing_pips * pip_value
        layer_entry = entry + offset if is_buy else entry - offset

        layer_tp_pips_val = tp_pips[i] if i < len(tp_pips) else tp_pips[-1]
        layer_tp = layer_entry + (layer_tp_pips_val * pip_value) if is_buy else layer_entry - (layer_tp_pips_val * pip_value)  # noqa: E501

        risk = risk_split[i] if i < len(risk_split) else risk_split[-1]

        layers.append({
            "entry": round(layer_entry, 2),
            "tp": round(layer_tp, 2),
            "risk_pct": round(risk, 2),
        })

    return layers


def enrich_signal_with_layers(
    sig: dict,
    layer_count: int = 3,
    layer_spacing: float = 50.0,
) -> dict:
    """Add layered entries to an existing signal dict."""
    action = sig.get("action", "HOLD")
    if action == "HOLD":
        return sig

    entry = sig.get("entry", 0)
    sl = sig.get("sl", 0)
    tp = sig.get("tp", 0)
    symbol = sig.get("symbol", "XAUUSD")

    if entry <= 0 or sl <= 0:
        return sig

    layers = generate_layers(
        action=action, entry=entry, sl=sl, tp=tp,
        symbol=symbol, layer_count=layer_count,
        layer_spacing_pips=layer_spacing,
    )

    sig["layers"] = layers
    sig["risk_percent"] = sig.get("risk_percent", 1.0)
    source = sig.get("source", "vtfx")
    sig["comment"] = f"VTFX/{source}/{layer_count}L"
    return sig


class LayeringEngine(Engine):
    """Smart Layering Engine.

    Generates multi-layer entry plans from a single signal.
    This engine acts as a post-processing layer — it takes an existing
    signal and produces a layered execution plan.
    """

    def __init__(self) -> None:
        self._default_layer_count: int = int(getattr(settings, "LAYERING_COUNT", 3))
        self._default_spacing: float = float(getattr(settings, "LAYERING_SPACING_PIPS", 50.0))

    @property
    def name(self) -> str:
        return "layering"

    async def analyze(self, ticks: list[Tick]) -> Signal | None:
        """Analyze ticks and produce a layering plan.

        Note: This engine primarily enriches existing signals with
        layer information. As a standalone engine, it generates a
        simple signal with layering metadata.
        """
        if not ticks or len(ticks) < 1:
            LOG.debug("Layering: insufficient ticks")
            return None

        try:
            current_price = ticks[-1].price

            # Generate a default layering plan around current price
            # In practice, this engine enriches signals from other engines
            risk_split = [0.4, 0.3, 0.3]
            tp_pips = [100.0, 200.0, 300.0]
            layers = generate_layers(
                action="BUY",
                entry=current_price,
                sl=current_price - _pip_value() * 150,  # ~15 pips SL for gold
                tp=current_price + _pip_value() * 200,  # ~20 pips TP
                layer_count=self._default_layer_count,
                layer_spacing_pips=self._default_spacing,
                risk_split=risk_split,
                tp_pips=tp_pips,
            )

            return Signal(
                symbol="XAUUSD",
                direction="CALL",
                predicted_digit=int(current_price * 10) % 10,
                confidence=0.5,
                source=SignalSource.MOMEN,
                grade=SignalGrade.MODERATE,
                metadata={
                    "engine": self.name,
                    "layers": layers,
                    "layer_count": self._default_layer_count,
                    "layer_spacing_pips": self._default_spacing,
                    "entry_price": current_price,
                    "layers_json": layers,
                },
            )
        except Exception as exc:
            LOG.warning("Layering engine error: %s", exc)
            raise SignalError("Layering analysis failed", details={"error": str(exc)}) from exc
