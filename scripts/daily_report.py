#!/usr/bin/env python3
"""Daily performance report for Vilona AI Trading System.
Self-contained: fetches APIs → formats message → prints to stdout.
Cron captures stdout and delivers to Telegram."""

import json
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

BRIDGE = "http://localhost:8765"
TARGET_SERVER_COST = 2_500_000  # Rp

WIB = timezone(timedelta(hours=7))


def fetch_json(path: str) -> dict | list:
    url = f"{BRIDGE}{path}"
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        return {"_error": str(e)}


def fmt_pnl(val: float) -> str:
    if val >= 0:
        return f"+${val:.2f}"
    return f"-${abs(val):.2f}"


def fmt_idr(val: int) -> str:
    if val >= 0:
        return f"+Rp{val:,}"
    return f"-Rp{abs(val):,}"


def fmt_pct(val: float) -> str:
    return f"{val:.1f}%"


def today_filter(trades: list) -> list:
    """Filter trades that were opened today (WIB timezone)."""
    today = datetime.now(WIB).date()
    result = []
    for t in trades:
        ot = t.get("open_time", "")
        if ot:
            try:
                dt = datetime.fromisoformat(ot)
                if dt.astimezone(WIB).date() == today:
                    result.append(t)
            except (ValueError, TypeError):
                pass
    return result


def build_report() -> str:
    # ── Fetch all data ──
    stats = fetch_json("/api/dash-stats")
    raw_trades = fetch_json("/api/trade-log")
    donations = fetch_json("/api/donations")

    now_str = datetime.now(WIB).strftime("%A, %d %B %Y · %H:%M WIB")
    xau_price = stats.get("xau_price", "--")
    if isinstance(xau_price, (int, float)):
        price_str = f"${xau_price:.2f}"
    else:
        price_str = str(xau_price)

    # ── Trades today ──
    if isinstance(raw_trades, list):
        today_trades = today_filter(raw_trades)
    elif isinstance(stats.get("trades"), list):
        today_trades = today_filter(stats["trades"])
    else:
        today_trades = []

    total_today = len(today_trades)

    if total_today > 0:
        wins = sum(1 for t in today_trades if t.get("profit_usd", 0) > 0)
        losses = sum(1 for t in today_trades if t.get("profit_usd", 0) < 0)
        win_rate = (wins / total_today) * 100 if total_today else 0.0
        total_profit = sum(t.get("profit_usd", 0) for t in today_trades)
        total_pips = sum(t.get("pips", 0) for t in today_trades)

        # Best and worst trades
        best_trade = max(today_trades, key=lambda t: t.get("profit_usd", 0))
        worst_trade = min(today_trades, key=lambda t: t.get("profit_usd", 0))
    else:
        wins = losses = 0
        win_rate = 0.0
        total_profit = 0.0
        total_pips = 0.0
        best_trade = worst_trade = None

    # ── Server cost progress ──
    raised = donations.get("total_raised", 0) if isinstance(donations, dict) else 0
    pct = (raised / TARGET_SERVER_COST) * 100 if TARGET_SERVER_COST > 0 else 0
    bar_len = 16
    filled = int((pct / 100) * bar_len)
    bar = "█" * filled + "░" * (bar_len - filled)

    # ── Format message ──
    lines = []
    lines.append(f"📊 *Vilona AI — Laporan Harian*")
    lines.append(f"━━━━━━━━━━━━━━━━━━━")
    lines.append(f"🕐 {now_str}")
    lines.append(f"💰 XAUUSD: *{price_str}*")
    lines.append("")

    lines.append(f"🏆 *Ringkasan Hari Ini*")
    lines.append(f"  • Total Sinyal: *{total_today}*")
    lines.append(f"  • Menang: {wins} | Kalah: {losses}")
    lines.append(f"  • Win Rate: *{fmt_pct(win_rate)}*")
    lines.append(f"  • P&L: *{fmt_pnl(total_profit)}* ({total_pips:+.1f} pips)")
    lines.append("")

    if best_trade:
        bt_action = best_trade.get("action", "?").upper()
        bt_profit = best_trade.get("profit_usd", 0)
        bt_pips = best_trade.get("pips", 0)
        bt_entry = best_trade.get("entry", "?")
        bt_outcome = best_trade.get("outcome", "?")
        lines.append(f"📈 *Best Trade*")
        lines.append(f"  • {bt_action} @ ${bt_entry} → {bt_outcome}")
        lines.append(f"  • Profit: *{fmt_pnl(bt_profit)}* ({bt_pips:+.1f} pips)")
        lines.append("")

    if worst_trade:
        wt_action = worst_trade.get("action", "?").upper()
        wt_profit = worst_trade.get("profit_usd", 0)
        wt_pips = worst_trade.get("pips", 0)
        wt_entry = worst_trade.get("entry", "?")
        wt_outcome = worst_trade.get("outcome", "?")
        lines.append(f"📉 *Worst Trade*")
        lines.append(f"  • {wt_action} @ ${wt_entry} → {wt_outcome}")
        lines.append(f"  • Loss: *{fmt_pnl(wt_profit)}* ({wt_pips:+.1f} pips)")
        lines.append("")

    # Server cost
    lines.append(f"💚 *Biaya Server Bulan Ini*")
    lines.append(f"  • Terkumpul: *Rp{raised:,}* / Rp{TARGET_SERVER_COST:,}")
    lines.append(f"  • Progress: `[{bar}]` {pct:.1f}%")
    lines.append("")

    # Overall stats
    lines.append(f"📊 *All-Time Stats*")
    lines.append(f"  • Total Sinyal: {stats.get('total', '--')}")
    lines.append(f"  • Win Rate: {fmt_pct(stats.get('win_rate', 0))}")
    lines.append(f"  • Total P&L: {fmt_pnl(stats.get('total_profit', 0))}")
    lines.append(f"  • EA Terhubung: {stats.get('ea_count', 0)}")
    uptime = stats.get("uptime", stats.get("uptime_seconds", "--"))
    lines.append(f"  • Uptime: {uptime}")
    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━")
    lines.append("🤖 *Vilona AI Trading System*")
    lines.append("💬 @berkahkaryaforexbotbot")

    return "\n".join(lines)


def main():
    report = build_report()
    print(report)


if __name__ == "__main__":
    main()
