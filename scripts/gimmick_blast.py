#!/usr/bin/env python3
"""gimmick_blast.py — Powerful conversion engine for Vilona Trade FX.

Modes:
  --weekly    Post weekly P&L to channel + DM free users
  --flash     Flash sale blast to all free users
  --freetier  Send 1 free S-TIER teaser to all free users

Cron examples:
  0 8 * * 1  python3 scripts/gimmick_blast.py --weekly   (Senin 08:00 WIB)
  0 10 * * 1  python3 scripts/gimmick_blast.py --flash     (Senin 10:00 — after weekly P&L)
  0 20 * * *  python3 scripts/gimmick_blast.py --freetier  (Setiap hari 20:00 — free users teaser)
"""

import sys
import os
import json
import time
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Load .env for bot token
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("gimmick-blast")

WIB = timezone(timedelta(hours=7))
PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "scripts"))
sys.path.insert(0, str(PROJECT_DIR))

# Telegram config
CHANNEL_ID = "-1003257064212"
BOT_USERNAME = "@berkahkaryaforexbotbot"
USD_IDR = 16350

# ── DB Helpers ──
def _get_members_db():
    import sqlite3
    db_path = PROJECT_DIR / "data" / "vilona_tradefx" / "members.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def get_free_users():
    """Get all free trial users (starter/trial, not paid)."""
    db = _get_members_db()
    rows = db.execute(
        "SELECT chat_id, nama, username FROM members WHERE status != 'paid' OR tier = 'starter'"
    ).fetchall()
    db.close()
    return [dict(r) for r in rows]


def get_premium_count():
    db = _get_members_db()
    row = db.execute(
        "SELECT COUNT(*) as n FROM members WHERE status = 'paid'"
    ).fetchone()
    db.close()
    return row["n"] if row else 0


def get_tier_counts():
    db = _get_members_db()
    rows = db.execute(
        "SELECT tier, COUNT(*) as n FROM members GROUP BY tier"
    ).fetchall()
    db.close()
    return {r["tier"]: r["n"] for r in rows}


# ── P&L Stats ──
def get_weekly_stats():
    try:
        from trade_tracker import get_stats, get_recent_trades
        stats = get_stats()
        recent = get_recent_trades(5)
        return stats, recent
    except Exception as e:
        logger.warning(f"Trade tracker error: {e}")
        return None, []


def fmt_weekly_pnl(stats, recent):
    wins = stats["wins"]
    losses = stats["losses"]
    total = stats["total"]
    wr = stats["win_rate"]
    total_pips = stats["total_pips"]
    total_usd = stats.get("total_profit_usd", stats.get("micro_profit", 0))
    
    if wr >= 60:
        perf_emoji, grade = "🟢", "BULLISH"
    elif wr >= 45:
        perf_emoji, grade = "🟡", "NEUTRAL"
    else:
        perf_emoji, grade = "🔴", "ROUGH WEEK"

    lines = [
        "📊 <b>WEEKLY S-TIER PERFORMANCE</b>",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"🗓 {(datetime.now(WIB) - timedelta(days=7)).strftime('%d %b')} — {datetime.now(WIB).strftime('%d %b %Y')}",
        "",
        f"{perf_emoji} <b>Win Rate: {wr:.1f}%</b> — {grade}",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"📈 Total Signals: {total}",
        f"✅ Wins: {wins} | ❌ Losses: {losses}",
        f"📐 Total Pips: {total_pips:+.1f}",
        f"💰 <b>Net P&L: ${total_usd:+,.2f}</b>",
        f"💵 ≈ Rp {total_usd * USD_IDR:+,.0f}",
        "━━━━━━━━━━━━━━━━━━━━━━",
    ]

    if recent:
        lines.append("📋 <b>5 TRADE TERAKHIR:</b>")
        for t in recent:
            emoji = "✅" if t.get("outcome") in ("TP_HIT", "WON") else "❌"
            pips = t.get("pips", 0)
            profit = t.get("profit_usd", 0)
            lines.append(f"  {emoji} {t.get('action','?')} {t.get('symbol','?')} | {pips:+.0f} pip | ${profit:+.0f}")
        lines.append("")

    lines.extend([
        "━━━━━━━━━━━━━━━━━━━━━━",
        "👑 <b>PREMIUM MEMBERS ALREADY PROFITED</b>",
        "",
        "🔥 Mau sinyal ini real-time setiap hari?",
        "⭐ <b>/subscribe PRO</b> — Rp50rb/bulan (20 sinyal/hari)",
        "💀 <b>/subscribe ELITE</b> — Rp150rb/bulan (Unlimited + GPT-4o + Grok + S-TIER)",
        "",
        "⚠️ <i>Past performance ≠ future results.</i>",
    ])

    return "\n".join(lines)


def fmt_flash_sale(free_count: int, premium_count: int) -> str:
    slots_left = max(3, 15 - premium_count)
    return "\n".join([
        "⚡ <b>FLASH PRO SALE — 24 JAM ONLY!</b>",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "",
        f"👥 <b>{free_count}</b> free users — <b>{slots_left} slot</b> tersisa!",
        "",
        "🔥 <b>HARGA HARI INI:</b>",
        "⭐ PRO: <b>Rp25rb</b>/bulan (normal Rp50rb)",
        "   → 50% OFF — 20 sinyal/hari",
        "",
        "💀 ELITE: <b>Rp75rb</b>/bulan (normal Rp150rb)",
        "   → 50% OFF — Unlimited + GPT-4o + Grok + S-TIER",
        "",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "⏰ <b>HANYA 24 JAM — SLOT TERBATAS</b>",
        "🔑 Harga naik setelah slot penuh atau besok.",
        "",
        "👉 <b>/subscribe</b> sekarang sebelum kehabisan!",
        "",
        f"📲 DM {BOT_USERNAME}",
    ])


def fmt_free_teaser(stats, recent) -> str:
    if stats:
        wr = stats["win_rate"]
        wins = stats["wins"]
        losses = stats["losses"]
        total_usd = stats.get("total_profit_usd", 0)
    else:
        wr, wins, losses, total_usd = 0, 0, 0, 0

    return "\n".join([
        "🎁 <b>1 SINYAL GRATIS BUAT LO — dari Vilona</b>",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "",
        "Lo belum pernah ngerasain S-TIER.",
        "",
        f"📊 <b>TRACK RECORD MINGGU INI:</b>",
        f"   ✅ {wins} Wins | ❌ {losses} Losses",
        f"   📈 Win Rate: {wr:.1f}%",
        f"   💰 Net P&L: ${total_usd:+,.0f}",
        "",
        "🔥 <b>S-TIER SIGNAL LO HARI INI:</b>",
        "   💀 Triple Confluence SMC",
        "   🔬 SnR Precision Entry",
        "",
        "⚠️ <i>Signal dikirim terpisah — cek DM berikutnya.</i>",
        "",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "💡 <b>Mau dapet ini SETIAP HARI?</b>",
        "",
        "⭐ <b>PRO — Rp25rb/bulan (FLASH SALE)</b>",
        "   /subscribe",
        "",
        "👑 87% user masih FREE — lo bisa ahead.",
    ])


# ── Send via Telegram ──
def tg_send(text: str, chat_id: str):
    """Send message via bot API using environment token."""
    token = os.environ.get("VILONA_BOT_TOKEN") or os.environ.get("BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        logger.error("No bot token found")
        return None

    import urllib.request
    import urllib.parse
    import urllib.error

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    }).encode()

    try:
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        if "429" in str(e):
            retry = int(e.headers.get("Retry-After", 5))
            logger.warning(f"Rate limited — waiting {retry}s")
            time.sleep(retry)
            return tg_send(text, chat_id)
        logger.error(f"HTTP {e.code}: {body[:200]}")
        return None
    except Exception as e:
        logger.error(f"Send error: {e}")
        return None


# ── Main ──
def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--weekly", action="store_true", help="Post weekly P&L + DM free users")
    ap.add_argument("--flash", action="store_true", help="Flash sale blast to free users")
    ap.add_argument("--freetier", action="store_true", help="Send free teaser to trial users")
    ap.add_argument("--dry-run", action="store_true", help="Print only, don't send")
    args = ap.parse_args()

    if not any([args.weekly, args.flash, args.freetier]):
        ap.print_help()
        return

    free_users = get_free_users()
    premium_n = get_premium_count()
    tier_counts = get_tier_counts()
    stats, recent = get_weekly_stats()

    logger.info(f"Users: {len(free_users)} free | {premium_n} premium")
    logger.info(f"Tiers: {tier_counts}")
    if stats:
        logger.info(f"Weekly: {stats.get('wins',0)}W/{stats.get('losses',0)}L | WR={stats.get('win_rate',0):.1f}%")

    # ── Weekly P&L Broadcast ──
    if args.weekly:
        if stats:
            text = fmt_weekly_pnl(stats, recent)
            if args.dry_run:
                print("=== WEEKLY P&L ===")
                print(text)
            else:
                result = tg_send(text, CHANNEL_ID)
                if result and result.get("ok"):
                    logger.info("✅ Weekly P&L posted to channel")
                else:
                    logger.error("❌ Weekly P&L post FAILED")
        else:
            logger.warning("No stats — skipping weekly P&L")

    # ── Flash Sale ──
    if args.flash:
        text = fmt_flash_sale(len(free_users), premium_n)
        if args.dry_run:
            print("\n=== FLASH SALE ===")
            print(text)
        else:
            # Post to channel
            result = tg_send(text, CHANNEL_ID)
            if result and result.get("ok"):
                logger.info("✅ Flash sale posted to channel")
            else:
                logger.error("❌ Flash sale post FAILED")

            # DM free users
            sent = 0
            for u in free_users:
                cid = str(u.get("chat_id", ""))
                if not cid or cid.startswith("test"):
                    continue
                try:
                    r = tg_send(text, cid)
                    if r and r.get("ok"):
                        sent += 1
                    time.sleep(0.35)
                except Exception as e:
                    logger.warning(f"Flash DM failed for {cid}: {e}")
            logger.info(f"✅ Flash sale DM'd to {sent}/{len(free_users)} free users")

    # ── Free Tier Teaser ──
    if args.freetier:
        text = fmt_free_teaser(stats, recent)
        if args.dry_run:
            print("\n=== FREE TEASER ===")
            print(text)
        else:
            sent = 0
            for u in free_users:
                cid = str(u.get("chat_id", ""))
                if not cid or cid.startswith("test"):
                    continue
                try:
                    r = tg_send(text, cid)
                    if r and r.get("ok"):
                        sent += 1
                    time.sleep(0.35)
                except Exception as e:
                    logger.warning(f"Teaser DM failed for {cid}: {e}")
            logger.info(f"✅ Free teaser DM'd to {sent}/{len(free_users)} free users")


if __name__ == "__main__":
    main()
