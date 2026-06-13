"""
Unified IDX Stock Analysis Engine.

Combines all analysis layers into a single enriched output:
    - Fundamental scoring (from idx_enricher)
    - Bandar accumulation detection (from idx_smart_money)
    - Anomaly detection (from idx_anomaly)
    - Backtest validation (from idx_backtest)

Usage::

    from tradebot.signals.idx_unified import unified_analysis

    result = await unified_analysis("BBCA")
    print(result.summary)
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from tradebot.signals.idx_anomaly import ANOMALY_SIGNAL_LABELS, AnomalyEngine
from tradebot.signals.idx_enricher import EnrichedStock, enrich
from tradebot.signals.idx_smart_money import SIGNAL_LABELS, BandarResult, SmartMoneyEngine

LOG = logging.getLogger("tradebot.signals.idx_unified")


@dataclass
class UnifiedResult:
    code: str
    name: str = ""
    sector: str = ""
    sub_sector: str = ""
    board: str = ""
    price: float = 0.0
    # Fundamental
    per: float = 0.0
    pbv: float = 0.0
    roe: float = 0.0
    eps: float = 0.0
    dividend_yield: float = 0.0
    market_cap: float = 0.0
    fundamental_score: int = 0
    valuation_grade: str = ""
    sector_avg_per: float = 0.0
    sector_avg_pbv: float = 0.0
    sector_avg_roe: float = 0.0
    # Smart Money
    bandar_score: int = 0
    bandar_signal: str = ""
    bandar_details: list[str] = field(default_factory=list)
    # Anomaly
    anomaly_type: str = "none"
    anomaly_signal: str = "HOLD"
    anomaly_score: float = 0.0
    anomaly_details: list[str] = field(default_factory=list)
    # Peers
    peers: list[str] = field(default_factory=list)
    # Errors
    errors: list[str] = field(default_factory=list)
    # Summary — formatted markdown output
    summary: str = ""


async def unified_analysis(symbol: str) -> UnifiedResult:
    """Run all analysis layers on a stock symbol.

    Returns UnifiedResult with complete analysis data + formatted summary.
    """
    from tradebot.signals.idx_encyclopedia import (
        get_board,
        get_name,
        get_peers,
        get_sector,
        get_sub_sector,
        is_idx_stock,
        resolve_code,
    )

    code = resolve_code(symbol)
    result = UnifiedResult(code=code)

    if not is_idx_stock(code):
        result.errors.append(f"{code} not found in IDX database")
        result.summary = f"❌ {code} tidak ditemukan di database IDX."
        return result

    result.name = get_name(code)
    result.sector = get_sector(code)
    result.sub_sector = get_sub_sector(code)
    result.board = get_board(code)
    result.peers = get_peers(code)[:10]

    # Run all engines concurrently
    fundamental_task = asyncio.create_task(_safe_enrich(code))
    bandar_task = asyncio.create_task(_safe_bandar(code))
    anomaly_task = asyncio.create_task(_safe_anomaly(code))

    fundamental, bandar, anomaly = await asyncio.gather(
        fundamental_task, bandar_task, anomaly_task
    )

    # Merge fundamental data
    if fundamental:
        result.price = fundamental.price
        result.per = fundamental.per
        result.pbv = fundamental.pbv
        result.roe = fundamental.roe
        result.eps = fundamental.eps
        result.dividend_yield = fundamental.dividend_yield
        result.market_cap = fundamental.market_cap
        result.fundamental_score = fundamental.fundamental_score
        result.valuation_grade = fundamental.valuation_grade
        result.sector_avg_per = fundamental.sector_avg_per
        result.sector_avg_pbv = fundamental.sector_avg_pbv
        result.sector_avg_roe = fundamental.sector_avg_roe
    else:
        result.errors.append("Fundamental data unavailable")

    # Merge bandar data
    if bandar:
        result.bandar_score = bandar.bandar_score
        result.bandar_signal = bandar.interpretation
        result.bandar_details = [
            SIGNAL_LABELS.get(s, s) for s in bandar.signals
        ]
    else:
        result.errors.append("Bandar analysis unavailable")

    # Merge anomaly data
    if anomaly:
        result.anomaly_type = anomaly.anomaly_type
        result.anomaly_signal = anomaly.signal
        result.anomaly_score = anomaly.anomaly_score
        result.anomaly_details = anomaly.details
    else:
        result.errors.append("Anomaly detection unavailable")

    # Build formatted summary
    result.summary = _build_summary(result)
    return result


async def _safe_enrich(code: str) -> EnrichedStock | None:
    try:
        return await enrich(code)
    except Exception as exc:
        LOG.warning("Enrich failed for %s: %s", code, exc)
        return None


async def _safe_bandar(code: str) -> BandarResult | None:
    try:
        engine = SmartMoneyEngine()
        return await engine.analyze(code)
    except Exception as exc:
        LOG.warning("Bandar failed for %s: %s", code, exc)
        return None


async def _safe_anomaly(code: str):
    try:
        engine = AnomalyEngine()
        return await engine.analyze(code)
    except Exception as exc:
        LOG.warning("Anomaly failed for %s: %s", code, exc)
        return None


# ── Summary Formatter ───────────────────────────────────────────────


def _build_summary(r: UnifiedResult) -> str:
    lines: list[str] = []

    # Header
    lines.append(f"📊 **{r.code} — {r.name}**")
    lines.append(f"`{r.sector}` / `{r.sub_sector}` | Board: `{r.board}`")
    lines.append("")

    # Price
    if r.price > 0:
        lines.append(f"💰 **Price:** Rp{r.price:,.0f}")
        if r.market_cap > 0:
            cap_t = r.market_cap / 1e12
            lines.append(f"🏢 **Market Cap:** Rp{cap_t:.0f}T")
        lines.append("")

    # Fundamental
    if r.per > 0:
        lines.append("📈 **Fundamental**")
        lines.append(f"PER: {r.per:.1f}x | PBV: {r.pbv:.2f}x | ROE: {r.roe:.1f}%")
        if r.eps > 0:
            lines.append(f"EPS: Rp{r.eps:,.0f} | Div Yield: {r.dividend_yield:.2f}%")
        if r.sector_avg_per > 0:
            lines.append(
                f"Sector Avg: PER {r.sector_avg_per:.1f}x | PBV {r.sector_avg_pbv:.2f}x | ROE {r.sector_avg_roe:.1f}%"
            )
        lines.append(f"Score: **{r.fundamental_score}/100** | {r.valuation_grade}")
        lines.append("")

    # Bandar
    if r.bandar_score > 0:
        emoji = "🐳" if r.bandar_score >= 60 else "📊"
        lines.append(f"{emoji} **Smart Money:** {r.bandar_score}/100 — {r.bandar_signal}")
        for detail in r.bandar_details[:3]:
            lines.append(f"   • {detail}")
        lines.append("")

    # Anomaly
    if r.anomaly_type != "none":
        label = ANOMALY_SIGNAL_LABELS.get(r.anomaly_type, r.anomaly_type)
        lines.append(f"🚨 **Anomaly:** {label}")
        for detail in r.anomaly_details[:2]:
            lines.append(f"   • {detail}")
        lines.append("")

    # Peers
    if r.peers:
        lines.append(f"👥 **Peers:** {', '.join(r.peers[:5])}")
        lines.append("")

    # Errors
    if r.errors:
        lines.append("⚠️ _" + ", ".join(r.errors) + "_")
        lines.append("")

    return "\n".join(lines)
