#!/usr/bin/env python3
"""Weekly + Daily Recap Script — sent at 00:10 WIB daily + weekly."""
import os, sys, json, time, logging
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("recap")

WIB = timezone(timedelta(hours=7))
CHANNEL_ID = int(os.environ.get("VILONA_MAPPING_CHANNEL", "-1003257064212"))
SESSION = os.path.expanduser("~/.openclaw/workspace/vilona_session")

try:
    from trade_tracker import get_daily_trades, get_stats, _load
    RECAP_AVAILABLE = True
except Exception as e:
    log.warning(f"trade_tracker unavailable: {e}")
    RECAP_AVAILABLE = False


def generate_daily_recap(date_str=""):
    """Generate daily recap — shows individual trades like competitor format."""
    if not RECAP_AVAILABLE:
        return "❌ Recap system unavailable."

    if not date_str:
        date_str = datetime.now(WIB).strftime("%Y-%m-%d")

    recap = get_daily_trades(date_str)
    stats = get_stats()

    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        day_names = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
        day_display = f"{day_names[dt.weekday()]}, {dt.strftime('%d %B %Y')}"
    except Exception:
        day_display = date_str

    total = recap["total_signals"]
    wins = recap["wins"]
    losses = recap["losses"]
    wr = recap["win_rate"]
    pips = recap["total_pips"]
    micro = recap["micro_profit"]
    micro_pct = recap["micro_profit_pct"]
    micro_idr = recap["micro_profit_idr"]

    perf_emoji = "🟢" if micro > 0 else "🔴" if micro < 0 else "⚪"

    lines = []
    lines.append(f"📊 <b>REKAP SINYAL HARIAN</b>")
    lines.append(f"🗓 {day_display}")
    lines.append(f"━━━━━━━━━━━━━━━━")
    lines.append("")

    # Individual trades
    daily_trades = recap.get("trades", [])
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

    # Simulasi modal $100
    lines.append(f"💵 <b>SIMULASI MODAL $100 (0.01 Lot):</b>")
    lines.append(f"   {perf_emoji} <b>${micro:+.2f}</b> (Rp {micro_idr:+,})")
    lines.append(f"   Return: <b>{micro_pct:+.1f}%</b> dalam 1 hari")
    lines.append("")

    # Overall stats
    lines.append(f"━━━━━━━━━━━━━━━━")
    lines.append(f"📈 <b>ALL-TIME:</b> {stats['wins']}W/{stats['losses']}L | WR: {stats['win_rate']:.1f}%")
    lines.append(f"Total Pips: {stats['total_pips']:+.1f}")
    lines.append("")
    lines.append(f"🤖 <i>AI-Powered by</i> <a href='https://t.me/vilonaaichanel'>Vilona Trade FX</a>")
    lines.append(f"📱 <a href='https://t.me/berkahkaryaforexbotbot'>@berkahkaryaforexbotbot</a> \u2014 Bot AI gratis 24/7")
    lines.append(f"")
    if micro > 0:
        lines.append(f"\U0001f4b0 <b>Profit hari ini Rp {micro_idr:+,}!</b>")
        lines.append(f"\U0001f9d1\u200d\U0001f4bb Kalau bermanfaat, subscribe ya bro 🚀! \U0001f91d")
        lines.append(f"\U0001f449 /subscribe \u2014 Dukung Server AI")
    elif micro < 0:
        lines.append(f"\U0001f4b8 <b>Rugi Rp {micro_idr:+,} hari ini.</b>")
        lines.append(f"\U0001f914 AI sedang belajar \u2014 dukung agar makin cerdas!")
        lines.append(f"\U0001f449 /subscribe \u2014 Dukung Server AI")
    else:
        lines.append(f"\U0001f449 /subscribe \u2014 Dukung Server AI")
    lines.append(f"")
    lines.append(f"<i>#VilonaTradeFX #DailyRecap #XAUUSD</i>")

    return "\n".join(lines)


def generate_weekly_recap():
    """Generate weekly recap — per-day breakdown with individual trades."""
    if not RECAP_AVAILABLE:
        return "❌ Recap system unavailable."

    now = datetime.now(WIB)
    data = _load()
    all_trades = data.get("trades", [])

    # Group by day (Mon-Fri only)
    day_names = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]

    lines = []
    lines.append(f"📊 <b>WEEKLY PERFORMANCE</b> 🗓")
    lines.append(f"━━━━━━━━━━━━━━━━")
    lines.append("")

    total_win_pips = 0
    total_loss_pips = 0
    total_wins = 0
    total_losses = 0

    # Get last 7 days
    for day_offset in range(6, -1, -1):
        day = now - timedelta(days=day_offset)
        day_str = day.strftime("%Y-%m-%d")
        day_trades = [t for t in all_trades if t.get("open_time", "").startswith(day_str)]
        
        if not day_trades:
            continue

        day_name = day_names[day.weekday()]
        lines.append(f"<b>{day_name} / {day.strftime('%d %b %Y')}</b>")

        day_win = 0
        day_loss = 0
        day_wins = 0
        day_losses = 0

        for t in day_trades:
            action = t.get("action", "?")
            symbol = t.get("symbol", "?")
            pips = t.get("pips", 0)
            outcome = t.get("outcome", "OPEN")

            if outcome == "OPEN":
                lines.append(f"⏳ {action} {symbol}: open...")
                continue

            emoji = "🟢" if outcome == "TP_HIT" else "🔴"
            lines.append(f"{emoji} {action} {symbol}: {pips:+.0f} Pips")

            if outcome == "TP_HIT":
                day_win += pips
                day_wins += 1
            else:
                day_loss += abs(pips)
                day_losses += 1

        total_win_pips += day_win
        total_loss_pips += day_loss
        total_wins += day_wins
        total_losses += day_losses

        lines.append(f"<b>Win: +{day_win:.0f} Pips ✅ | Loss: -{day_loss:.0f} Pips ❌</b>")
        lines.append("")

    net_pips = total_win_pips - total_loss_pips
    total_trades = total_wins + total_losses
    wr = (total_wins / total_trades * 100) if total_trades > 0 else 0

    lines.append(f"━━━━━━━━━━━━━━━━")
    lines.append(f"🏆 <b>WEEKLY TOTAL:</b>")
    lines.append(f"   ✅ Win Profit: <b>+{total_win_pips:.0f} Pips</b>")
    lines.append(f"   ❌ Loss: <b>-{total_loss_pips:.0f} Pips</b>")
    lines.append(f"   📊 WR: <b>{wr:.1f}%</b> ({total_wins}W/{total_losses}L)")
    lines.append(f"")
    lines.append(f"   💰 <b>Net All Win: {net_pips:+.0f} Pips</b>")
    lines.append(f"")
    lines.append(f"━━━━━━━━━━━━━━━━")
    lines.append(f"")
    lines.append(f"🤖 <i>AI-Powered by</i> <a href='https://t.me/vilonaaichanel'>Vilona Trade FX</a>")
    lines.append(f"📱 Join channel untuk sinyal live harian!")
    lines.append(f"")
    lines.append(f"<i>#VilonaTradeFX #WeeklyRecap #XAUUSD</i>")

    return "\n".join(lines)


def send_to_channel(text, label="recap"):
    """Send recap/mapping to channel via Telethon."""
    import asyncio
    from telethon import TelegramClient

    async def send():
        client = TelegramClient(SESSION, 23647272, "1f69a4e0f03e5f51ddfa5b67ac7b5c49")
        await client.connect()
        if not await client.is_user_authorized():
            log.error("Telethon not authorized!")
            return False
        try:
            msg = await client.send_message(CHANNEL_ID, text, parse_mode='html', link_preview=False)
            log.info(f"{label} sent! msg_id={msg.id}")
            return True
        except Exception as e:
            log.error(f"Failed to send {label}: {e}")
            return False
        finally:
            await client.disconnect()

    return asyncio.run(send())


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["daily","weekly","all"], default="daily")
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--date", default="")
    args = ap.parse_args()

    if args.mode in ("daily", "all"):
        text = generate_daily_recap(args.date)
        if args.send:
            send_to_channel(text, "daily-recap")
        else:
            print(text)

    if args.mode in ("weekly", "all"):
        text = generate_weekly_recap()
        if args.send:
            send_to_channel(text, "weekly-recap")
        else:
            print()
            print(text)
