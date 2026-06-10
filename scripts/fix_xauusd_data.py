#!/usr/bin/env python3
"""Delete wrong post and test data sources"""
import json, urllib.request, re, os, sys

PROJECT_DIR = "/home/openclaw/projects/1ai-trade-bot"
os.chdir(PROJECT_DIR)
sys.path.insert(0, PROJECT_DIR)

env = open("strategies/vilona_tradefx/.env").read()
tk = ""
for line in env.splitlines():
    if line.startswith("VILONA_TRADEFX_TELEGRAM_BOT_TOKEN"):
        tk = line.split("=",1)[1].strip().strip('"').strip("'")

# Delete wrong post ID 84
del_url = f"https://api.telegram.org/bot{tk}/deleteMessage"
pay = json.dumps({"chat_id": "-1003257064212", "message_id": 84}).encode()
req = urllib.request.Request(del_url, data=pay, headers={"Content-Type": "application/json"})
r = json.loads(urllib.request.urlopen(req, timeout=10).read())
print(f"Delete msg 84: {r}")

# Try market_data for XAUUSD (GC=F)
try:
    from market_data import UnifiedMarketData
    md = UnifiedMarketData()
    bars = md.get_bars_dicts("GC=F", "15m", 200)
    print(f"GC=F bars: {len(bars) if bars else 0}")
    if bars and len(bars) > 20:
        last = bars[-1]
        print(f"Last close: ${last.get('close', last.get('c', '?'))}")
except Exception as e:
    print(f"market_data: {e}")

# Try raw Yahoo
try:
    url = "https://query1.finance.yahoo.com/v8/finance/chart/GC=F?range=5d&interval=15m"
    r = urllib.request.urlopen(url, timeout=10)
    data = json.loads(r.read())
    if data.get("chart", {}).get("result"):
        res = data["chart"]["result"][0]
        qt = res["indicators"]["quote"][0]
        closes = [x for x in qt["close"] if x is not None]
        timestamps = res["timestamp"]
        print(f"Yahoo GC=F: {len(closes)} bars, last: ${closes[-1]:.2f}")
    else:
        err = data.get("chart", {}).get("error", "unknown error")
        print(f"Yahoo GC=F error: {err}")
except Exception as e:
    print(f"Yahoo GC=F exception: {e}")
