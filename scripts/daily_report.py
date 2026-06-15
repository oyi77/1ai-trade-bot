#!/usr/bin/env python3
"""Daily Performance Report — reads trade_tracker data, outputs formatted Telegram message."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '_legacy'))

from trade_tracker import format_winrate, get_daily_trades, get_stats
from datetime import datetime, timezone, timedelta

WIB = timezone(timedelta(hours=7))
now = datetime.now(WIB)
date_str = now.strftime("%Y-%m-%d")
day_names = ["Senin","Selasa","Rabu","Kamis","Jumat","Sabtu","Minggu"]
day_display = f"{day_names[now.weekday()]}, {now.strftime('%d %B %Y')}"

# Daily trades
daily = get_daily_trades(date_str)

# Build report
lines = []
lines.append(f"📊 <b>REKAP SINYAL HARIAN</b>")
lines.append(f"🗓 {day_display}")
lines.append(f"━━━━━━━━━━━━━━━━")
lines.append("")

total = daily["total_signals"]
wins = daily["wins"]
losses = daily["losses"]
wr = daily["win_rate"]
pips = daily["total_pips"]
micro = daily["micro_profit"]
micro_pct = daily["micro_profit_pct"]
micro_idr = daily["micro_profit_idr"]

perf_emoji = "🟢" if micro > 0 else "🔴" if micro < 0 else "⚪"

# Individual trades
daily_trades = daily.get("trades", [])
if daily_trades:
    lines.append("📡 <b>SINYAL HARI INI:</b>")
    lines.append("")
    for t in daily_trades:
        emoji = "✅" if t.get("outcome") == "TP_HIT" else "❌" if t.get("outcome") == "SL_HIT" else "⏳"
        action = t.get("action", "?")
        symbol = t.get("symbol", "?")
        pip_val = t.get("pips", 0)
        pip_str = f"+{pip_val:.0f} Pips" if pip_val > 0 else f"{pip_val:.0f} Pips"
        if t.get("outcome") == "OPEN":
            pip_str = "open..."
        lines.append(f"{emoji} {action} {symbol}: {pip_str}")
    lines.append("")

# Summary
lines.append(f"━━━━━━━━━━━━━━━━")
lines.append(f"📊 <b>Total Sinyal:</b> {total} | ✅ {wins}W / ❌ {losses}L | WR: {wr:.1f}%")
lines.append(f"📐 <b>Total Pips:</b> {pips:+.1f}")
lines.append("")

lines.append(f"💵 <b>SIMULASI MODAL $100 (0.01 Lot):</b>")
lines.append(f"   {perf_emoji} <b>${micro:+.2f}</b> (Rp {micro_idr:+,})")
lines.append(f"   Return: <b>{micro_pct:+.1f}%</b> dalam 1 hari")
lines.append("")

# All-time stats
stats = get_stats()
lines.append(f"━━━━━━━━━━━━━━━━")
lines.append(f"📈 <b>ALL-TIME:</b> {stats['wins']}W/{stats['losses']}L | WR: {stats['win_rate']:.1f}%")
lines.append(f"Total Pips: {stats['total_pips']:+.1f}")
lines.append(f"Profit: <b>${stats['total_profit_usd']:+,.2f}</b> (Rp {stats['total_profit_idr']:+,})")
lines.append("")

lines.append(f"🤖 <i>AI-Powered by</i> <a href='https://t.me/vilonaaichanel'>Vilona Trade FX</a>")
lines.append(f"📱 <a href='https://t.me/berkahkaryaforexbotbot'>@berkahkaryaforexbotbot</a> — Bot AI gratis 24/7")
lines.append("")

if micro > 0:
    lines.append(f"💪 <b>Profit hari ini Rp {micro_idr:+,}!</b>")
    lines.append(f"🧑‍💻 Kalau bermanfaat, subscribe ya bro 🚀! 🤝")
elif micro < 0:
    lines.append(f"📉 <b>Rugi Rp {micro_idr:+,} hari ini.</b>")
    lines.append(f"🤔 AI sedang belajar — dukung agar makin cerdas!")
else:
    lines.append(f"👉 /subscribe — Dukung Server AI")

lines.append("")
lines.append(f"<i>#VilonaTradeFX #DailyRecap #XAUUSD</i>")

print("\n".join(lines))
