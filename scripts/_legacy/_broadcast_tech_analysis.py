#!/usr/bin/env python3
"""Broadcast Tech Analysis Terminal announcement to all bot members."""
import sqlite3, json, urllib.request, time, os, sys

os.chdir("/home/openclaw/projects/1ai-trade-bot")

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

print(f"Total members: {len(rows)}, real users: {len(real_users)}")

MSG = (
    "🆕 <b>FITUR BARU — TECHNICAL ANALYSIS TERMINAL</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "Sekarang lu bisa analisa teknikal SMC\n"
    "secara <b>deterministic (no AI hallucination)</b>:\n\n"
    "🧲 <b>/zones</b> — Order Blocks + FVG + Supply/Demand\n"
    "🏗 <b>/structure</b> — BOS/CHoCH + Trend + MTF Alignment\n"
    "🕐 <b>/session</b> — Killzone + Session High/Low + Range\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n"
    "🆓 <b>FREE:</b> Basic analysis (1 Timeframe)\n"
    "👑 <b>DONOR:</b> Multi-TF + Full Depth Analysis\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n"
    "🔑 <b>Cara pake:</b> DM bot → ketik command\n"
    "   <code>/zones xauusd</code>\n"
    "   <code>/structure xauusd</code>\n"
    "   <code>/session xauusd</code>\n\n"
    "📌 Support: XAUUSD · BTCUSD · ETHUSD · USOIL · Forex\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n"
    "💡 <i>Tools analisa, bukan sinyal.</i>\n"
    "   Lu yang baca struktur, lu yang decide entry.\n\n"
    "👑 Multi-TF + Full Depth → <b>/subscribe</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n"
    "⚡ Cobain sekarang — ketik /zones xauusd"
)

sent = 0
failed = 0
skipped = 0
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
            print(f"  ✅ {cid} ({status})")
        else:
            err = resp.get("description", "?")
            # Blocked/bot can't initiate = skip gracefully
            if "bot can't" in err.lower() or "blocked" in err.lower() or "chat not found" in err.lower():
                skipped += 1
                print(f"  ⏭️ {cid} skipped: {err[:60]}")
            else:
                failed += 1
                print(f"  ❌ {cid}: {err[:80]}")
        time.sleep(0.6)  # rate limit safety
    except Exception as e:
        failed += 1
        print(f"  💥 {cid}: {e}")

print(f"\n═══ BROADCAST DONE ═══")
print(f"✅ Sent: {sent}")
print(f"⏭️ Skipped (blocked/not found): {skipped}")
print(f"❌ Failed: {failed}")
