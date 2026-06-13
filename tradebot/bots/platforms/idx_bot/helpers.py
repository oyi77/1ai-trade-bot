"""
IDX Bot — Telegram output formatters.

Format numbers, prices, and structured output for Telegram HTML.
"""

from __future__ import annotations


def format_price(price: float) -> str:
    if price >= 1000:
        return f"Rp{price:,.0f}"
    return f"Rp{price:.0f}"


def format_money(value: float) -> str:
    if value >= 1e12:
        return f"Rp{value/1e12:.1f}T"
    if value >= 1e9:
        return f"Rp{value/1e9:.1f}M"
    if value >= 1e6:
        return f"Rp{value/1e6:.0f}JT"
    return f"Rp{value:,.0f}"


def format_percent(value: float) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.1f}%"


def format_fundamental_output(result) -> str:
    """Format fundamental analysis section."""
    lines = []

    if result.price > 0:
        lines.append(f"💰 <b>Harga:</b> {format_price(result.price)}")

    if result.market_cap > 0:
        lines.append(f"🏢 <b>Market Cap:</b> {format_money(result.market_cap)}")

    lines.append("")

    if result.per > 0:
        lines.append("<b>📈 Fundamental:</b>")
        lines.append(
            f"PER: <b>{result.per:.1f}x</b> | PBV: <b>{result.pbv:.2f}x</b> | ROE: <b>{result.roe:.1f}%</b>"
        )

        if result.eps > 0:
            lines.append(
                f"EPS: {format_price(result.eps)} | Div Yield: <b>{result.dividend_yield:.2f}%</b>"
            )

        if result.sector_avg_per > 0:
            lines.append(
                f"<i>Sector Avg:</i> PER {result.sector_avg_per:.1f}x | "
                f"PBV {result.sector_avg_pbv:.2f}x | ROE {result.sector_avg_roe:.1f}%"
            )

        lines.append(
            f"Score: <b>{result.fundamental_score}/100</b> | {result.valuation_grade}"
        )

    lines.append("")
    return "\n".join(lines)


def format_bandar_output(result) -> str:
    """Format smart money / bandar section."""
    from tradebot.signals.idx_smart_money import SIGNAL_LABELS

    lines = ["", f"🐳 <b>Smart Money:</b> {result.bandar_score}/100 — {result.bandar_signal}"]
    for detail in result.bandar_details[:3]:
        lines.append(f"   • {detail}")
    lines.append("")
    return "\n".join(lines)


def format_peer_output(result) -> str:
    """Format peer comparison section."""
    lines = ["", "👥 <b>Peers:</b>"]
    lines.append(f"   {', '.join(result.peers[:5])}")
    lines.append("")
    return "\n".join(lines)


def format_screener_output(stocks: list[dict]) -> str:
    """Format screener output for multiple stocks."""
    lines = ["📊 <b>Market Overview</b>", ""]
    for s in stocks:
        code = s.get("code", "?")
        price = s.get("price", 0)
        per = s.get("per", 0)
        score = s.get("score", 0)
        lines.append(
            f"<code>{code}</code> {format_price(price)} | "
            f"PER {per:.1f}x | Score {score}"
        )
    return "\n".join(lines)
