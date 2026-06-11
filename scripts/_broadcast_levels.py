#!/usr/bin/env python3
"""Broadcast /levels feature announcement to all bot members."""
import sqlite3, json, urllib.request, time, os, sys

os.chdir("/home/openclaw/projects/1ai-trade-bot")

# Load token from env file
env_file = "strategies/vilona_tradefx/.env"
token = ""
if os.path.exists(env_file):
    with open(env_file) as f:
        for line in f:
            if line.startswith("VILONA_TRADEFX_TELEGRAM_BOT_TOKEN="):
                token = line.split("=", 1)[1].strip()
                break
if not token:
    print("NO TOKEN")
    sys.exit(1)

DB = "data/vilona_tradefx/members.db"
conn = sqlite3.connect(DB)
rows = conn.execute("SELECT chat_id, status FROM members").fetchall()
conn.close()

real_users = []
for cid, status in rows:
    try:
        cid_int = int(cid)
        if cid_int > 0:
            real_users.append((str(cid_int), status))
    except:
        pass

print(f"Members: {len(rows)}, real: {len(real_users)}")

MSG = (
    "🚀 <b>FITUR BARU! /levels — SnR + FIBO + Engine Deep Dive</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n"
    "Sekarang kamu bisa analisa level support/resistance\n"
    "langsung dari bot!\n\n"
    "📐 <b>Layer 1: Simple SnR + FIBO</b>\n"
    "• Support & Resistance dengan multi-touch confirmation\n"
    "• FIBO 38.2% / 50% / 61.8%\n"
    "• Rekomendasi SL placement (aman dari wick)\n\n"
    "🏦 <b>Layer 2: Engine Deep Dive</b>\n"
    "• SMC Order Blocks\n"
    "• Fair Value Gaps (FVG)\n"
    "• Liquidity Zones\n"
    "• Session Levels\n\n"
    "👑 <b>Fitur Premium — Khusus Subscriber</b>\n"
    "Free member bisa lihat command, akses penuh\n"
    "setelah subscribe.\n\n"
    "🔥 Cobain sekarang: /levels xauusd\n"
    "💚 Support AI: /subscribe"
)

sent = 0
failed = 0
for cid, status in real_users:
    try:
        payload = json.dumps({
            "chat_id": cid,
            "text": MSG,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            resp = json.loads(r.read())
        if resp.get("ok"):
            sent += 1
            print(f"  OK {cid}")
        else:
            failed += 1
            print(f"  FAIL {cid}: {resp.get('description', '?')}")
        time.sleep(0.5)
    except Exception as e:
        failed += 1
        print(f"  ERR {cid}: {e}")

print(f"\nDone: {sent} sent, {failed} failed")
