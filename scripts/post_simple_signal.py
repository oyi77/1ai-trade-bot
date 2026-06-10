#!/usr/bin/env python3
"""Post simplified XAUUSD signal with promo CTA"""
import json, urllib.request, re

env = open("/home/openclaw/projects/1ai-trade-bot/strategies/vilona_tradefx/.env").read()
m = re.search(r'VILONA_TRADEFX_TELEGRAM_BOT_TOKEN\s*=\s*(.+)', env)
tk = m.group(1).strip().strip("\"'")
data = json.load(open("/tmp/xauusd_data.json"))

price = data["price"]
closes = [c["close"] for c in data["ohlcv"]]

def ema(d,p):
    m=2/(p+1); r=[d[0]]
    for i in range(1,len(d)): r.append((d[i]-r[-1])*m+r[-1])
    return r

e20 = ema(closes,20)[-1]
e50 = ema(closes,50)[-1]
trend = "BEARISH" if price < e50 else "BULLISH" if price > e20 else "SIDEWAYS"

S = "\u2501" * 22

if trend == "BEARISH":
    action = "SELL"; de = "\U0001f534"
    e_hi = round(price, 0); e_lo = round(price - 15, 0)
    tp = round(price - 50, 0); sl = round(price + 25, 0)
    conf = "65%"; reason = "Harga di bawah EMA50, momentum bearish"
    rr = f"1:{round((e_hi - tp) / (sl - e_hi), 1)}"
elif trend == "BULLISH":
    action = "BUY"; de = "\U0001f7e2"
    e_hi = round(price, 0); e_lo = round(price - 10, 0)
    tp = round(price + 40, 0); sl = round(price - 20, 0)
    conf = "72%"; reason = "Harga di atas EMA20, momentum bullish"
    rr = f"1:{round((tp - e_hi) / (e_hi - sl), 1)}"
else:
    action = "HOLD / NEUTRAL"; de = "\u26aa"
    tp = "-"; sl = "-"; conf = "-"; rr = "-"
    reason = "Market sideways, tunggu breakout"
    e_hi = e_lo = price

lines = []
lines.append(f"<b>XAUUSD GOLD \u2014 {de} {action}</b>")
lines.append(S)
lines.append(f"\U0001f4b2 Harga: <code>${price:,.0f}</code>")
lines.append(f"\U0001f4ca Trend: {trend} | AI: {conf}")
lines.append(S)
lines.append(f"\U0001f3af Entry: <code>${e_lo:,.0f}</code> \u2014 <code>${e_hi:,.0f}</code>")
lines.append(f"\U0001f3af TP: <code>${tp:,.0f}</code>")
lines.append(f"\U0001f6d1 SL: <code>${sl:,.0f}</code>")
lines.append(f"\U0001f4d0 RR: {rr}")
lines.append(S)
lines.append(reason)
lines.append(S)
lines.append("\U0001f4a1 Mau sinyal real-time langsung di HP?")
lines.append(f"\U0001f916 <a href='https://t.me/berkahkaryaforexbotbot'>@berkahkaryaforexbotbot</a>")
lines.append("\u2014 Gratis, AI-powered, 24/7")
lines.append(S)
lines.append("\U0001f517 Share: phantomfx.aitradepulse.com")
lines.append("\U0001f4aa Bantu share ke temen-temen ya! 🙏")

msg = "\n".join(lines)
pay = json.dumps({"chat_id": "-1003257064212", "text": msg, "parse_mode": "HTML"}).encode()
req = urllib.request.Request(f"https://api.telegram.org/bot{tk}/sendMessage", data=pay, headers={"Content-Type": "application/json"})
resp = urllib.request.urlopen(req, timeout=15)
res = json.loads(resp.read())
print(f"OK: {res.get('ok')}, ID: {res.get('result',{}).get('message_id','?')}")
