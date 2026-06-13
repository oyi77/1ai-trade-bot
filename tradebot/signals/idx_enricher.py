"""
IDX Stock Enrichment Engine.

Combines Yahoo Finance fundamentals with IDX encyclopedia data to produce
enriched analysis including:

* Sector classification & peer comparison
* Fundamental scoring (PER, PBV, ROE relative to sector)
* Board classification
* Composite quality score

Usage::

    from tradebot.signals.idx_enricher import enrich

    result = await enrich("BBCA")
    # result.sector = "Keuangan"
    # result.peers = ["BBRI", "BMRI", "BBNI", "BRIS"]
    # result.fundamental_score = 85
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

import yfinance as yf  # type: ignore[import-untyped]

from tradebot.signals.idx_encyclopedia import (
    get_board,
    get_name,
    get_peers,
    get_sector,
    get_sub_sector,
    is_idx_stock,
    resolve_code,
)

LOG = logging.getLogger("tradebot.signals.idx_enricher")


@dataclass
class EnrichedStock:
    code: str
    name: str = ""
    sector: str = ""
    sub_sector: str = ""
    board: str = ""
    peers: list[str] = field(default_factory=list)
    price: float = 0.0
    market_cap: float = 0.0
    per: float = 0.0
    pbv: float = 0.0
    roe: float = 0.0
    eps: float = 0.0
    dividend_yield: float = 0.0
    beta: float = 0.0
    sector_avg_per: float = 0.0
    sector_avg_pbv: float = 0.0
    sector_avg_roe: float = 0.0
    fundamental_score: int = 0
    valuation_grade: str = ""


async def enrich(symbol: str) -> EnrichedStock | None:
    """Enrich a stock symbol with sector context and peer comparison."""
    code = resolve_code(symbol)
    if not is_idx_stock(code):
        return None

    yahoo_symbol = f"{code}.JK"

    # Fetch Yahoo Finance data in thread pool
    ticker = await asyncio.to_thread(yf.Ticker, yahoo_symbol)
    info = await asyncio.to_thread(lambda t: t.info, ticker)

    stock = EnrichedStock(code=code)
    stock.name = get_name(code)
    stock.sector = get_sector(code)
    stock.sub_sector = get_sub_sector(code)
    stock.board = get_board(code)
    stock.peers = get_peers(code)[:20]

    # Prices
    stock.price = _safe_float(info.get("currentPrice", info.get("regularMarketPrice", 0)))
    stock.market_cap = _safe_float(info.get("marketCap", 0))

    # Fundamentals
    stock.per = _safe_float(info.get("trailingPE", info.get("forwardPE", 0)))
    stock.pbv = _safe_float(info.get("priceToBook", 0))
    roe_raw = info.get("returnOnEquity")
    stock.roe = _safe_float(roe_raw) * 100 if roe_raw and _safe_float(roe_raw) < 1 else _safe_float(roe_raw)
    stock.eps = _safe_float(info.get("trailingEps", 0))
    div_raw = info.get("dividendYield")
    stock.dividend_yield = _safe_float(div_raw) * 100 if div_raw and _safe_float(div_raw) < 1 else _safe_float(div_raw)
    stock.beta = _safe_float(info.get("beta", 0))

    # Sector comparison
    await _compute_sector_averages(stock)
    stock.fundamental_score = _compute_fundamental_score(stock)
    stock.valuation_grade = _compute_valuation_grade(stock)

    return stock


async def _compute_sector_averages(stock: EnrichedStock) -> None:
    """Compute sector median fundamentals from peer group."""
    peers = stock.peers[:10]
    if not peers:
        return

    pers, pbvs, roes = [], [], []
    for peer_code in peers:
        try:
            ticker = await asyncio.to_thread(yf.Ticker, f"{peer_code}.JK")
            info = await asyncio.to_thread(lambda t: t.info, ticker)
            p = _safe_float(info.get("trailingPE", 0))
            b = _safe_float(info.get("priceToBook", 0))
            r = _safe_float(info.get("returnOnEquity", 0))
            r = r * 100 if r < 1 else r
            if p > 0:
                pers.append(p)
            if b > 0:
                pbvs.append(b)
            if r > 0:
                roes.append(r)
        except Exception:
            continue

    if pers:
        pers.sort()
        stock.sector_avg_per = pers[len(pers) // 2]
    if pbvs:
        pbvs.sort()
        stock.sector_avg_pbv = pbvs[len(pbvs) // 2]
    if roes:
        roes.sort()
        stock.sector_avg_roe = roes[len(roes) // 2]


def _compute_fundamental_score(stock: EnrichedStock) -> int:
    """Score 0-100 based on fundamentals relative to sector."""
    score = 50

    # PER: lower is better (max +25 pts)
    if stock.per > 0 and stock.sector_avg_per > 0:
        ratio = stock.sector_avg_per / stock.per
        score += min(25, int(ratio * 10))

    # PBV: lower is better (max +15 pts)
    if stock.pbv > 0 and stock.sector_avg_pbv > 0:
        ratio = stock.sector_avg_pbv / stock.pbv
        score += min(15, int(ratio * 5))

    # ROE: higher is better (max +25 pts)
    if stock.roe > 0 and stock.sector_avg_roe > 0:
        ratio = stock.roe / stock.sector_avg_roe
        score += min(25, int(ratio * 10))

    # Dividend yield bonus (max +10 pts)
    if stock.dividend_yield > 2:
        score += min(10, int(stock.dividend_yield))

    # Board premium (Utama = +5)
    if stock.board == "Utama":
        score += 5

    return min(100, max(0, score))


def _compute_valuation_grade(stock: EnrichedStock) -> str:
    """Grade the stock's valuation relative to sector."""
    if stock.per <= 0 or stock.sector_avg_per <= 0:
        return "?"
    ratio = stock.per / stock.sector_avg_per
    if ratio < 0.5:
        return "💰 Deep Value"
    if ratio < 0.7:
        return "🟢 Undervalued"
    if ratio < 0.9:
        return "🟡 Fair Value"
    if ratio < 1.5:
        return "🟠 Premium"
    return "🔴 Overvalued"


def _safe_float(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        return 0.0
