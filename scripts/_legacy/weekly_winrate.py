#!/usr/bin/env python3
"""weekly_winrate.py — Auto-post weekly winrate ke channel setiap Senin 08:00 WIB."""
import sys, os, json, logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("weekly-winrate")

WIB = timezone(timedelta(hours=7))
PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "scripts"))

try:
    from trade_tracker import get_stats, get_recent_trades
except ImportError:
    logger.error("Cannot import trade_tracker")
    sys.exit(1)

USD_IDR = 16350

def fmt():
    stats = get_stats()
    wins = stats["wins"]
    losses = stats["losses"]
    total = stats["total"]
    wr = stats["win_rate"]
    total_pips = stats["total_pips"]
    total_usd = stats["total_profit_usd"]
    total_idr = stats["total_profit_idr"] or round(total_usd * USD_IDR)
    best_win = stats.get("best_win_pips", 0)
    worst_loss = stats.get("worst_loss_pips", 0)
    open_pos = stats["open_positions"]

    if wr >= 55:
        perf_emoji, grade = "🟢", "EXCELLENT"
    elif wr >= 40:
        perf_emoji, grade = "🟡", "DECENT"
    else:
        perf_emoji, grade = "🔴", "NEED IMPROVEMENT"

    profit_idr_str = f"Rp {total_idr:+,}"
    if total_idr > 1_000_000:
        profit_idr_str += f" (Rp {total_idr/1_000_000:.1f}jt)"

    lines = [
        "📊 <b>WEEKLY PERFORMANCE REPORT</b>",
        f"━━━━━━━━━━━━━━━━━━━━━━",
        f"🗓 Minggu ini | Auto-generated",
        f"",
        f"{perf_emoji} <b>Win Rate: {wr:.1f}%</b> — {grade}",
        f"━━━━━━━━━━━━━━━━━━━━━━",
        f"📈 Total Signals: {total}",
        f"✅ Wins: {wins} | ❌ Losses: {losses}",
        f"📐 Total Pips: {total_pips:+.1f}",
        f"━━━━━━━━━━━━━━━━━━━━━━",
        f"💰 <b>Profit: ${total_usd:+,.2f}</b>",
        f"💵 {profit_idr_str}",
    ]
    if best_win > 0:
        lines.append(f"🏆 Best Win: +{best_win:.1f} pips")
    if worst_loss > 0:
        lines.append(f"⚠️ Worst Loss: {worst_loss:.1f} pips")
    if open_pos > 0:
        lines.append(f"🔓 Open Positions: {open_pos}")

    # Recent trades (last 5)
    recent = get_recent_trades(5)
    if recent:
        lines.append(f"")
        lines.append(f"📋 <b>5 TRADE TERAKHIR:</b>")
        for t in recent:
            emoji = "✅" if t["outcome"] == "TP_HIT" else "❌"
            pips = t.get("pips", 0)
            lines.append(f"  {emoji} {t.get('action','?')} {t.get('symbol','?')} | {pips:+.0f} pip | ${t.get('profit_usd',0):+.0f}")

    lines.append(f"")
    lines.append(f"━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"🔥 Mau dapetin sinyal ini real-time?")
    lines.append(f"⚡ <b>/subscribe</b> — Rp50rb/bulan (PRO)")
    lines.append(f"👑 <b>/subscribe</b> — Rp150rb/bulan (ELITE + GPT-4o + Grok)")
    lines.append(f"")
    lines.append(f"⚠️ <i>Past performance ≠ future results. NFA.</i>")

    return "\n".join(lines)

if __name__ == "__main__":
    text = fmt()
    print(text)
