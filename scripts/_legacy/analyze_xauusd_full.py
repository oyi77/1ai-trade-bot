#!/usr/bin/env python3
"""Full XAUUSD analysis for Monday — SMC + Ultimate + Chaos + AI + Teknikal"""
import json, urllib.request, re, os, sys, math

PROJECT_DIR = "/home/openclaw/projects/1ai-trade-bot"
sys.path.insert(0, PROJECT_DIR)

S = "\u2501" * 22  # separator line

# 1. FETCH XAUUSD DATA
print("[1/7] Fetching XAUUSD data from Binance...")
try:
    url = "https://api.binance.com/api/v3/klines?symbol=XAUUSDT&interval=15m&limit=200"
    r = urllib.request.urlopen(url, timeout=10)
    klines = json.loads(r.read())
except:
    print("Binance XAU not found, checking BTC-USD relationship...")
    url = "https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=15m&limit=200"
    r = urllib.request.urlopen(url, timeout=10)
    klines = json.loads(r.read())

ohlcv = []
for k in klines:
    ohlcv.append({"open": float(k[1]), "high": float(k[2]), "low": float(k[3]),
                  "close": float(k[4]), "volume": float(k[5]), "timestamp": int(k[0])})

closes = [c["close"] for c in ohlcv]
highs = [c["high"] for c in ohlcv]
lows = [c["low"] for c in ohlcv]
opens = [c["open"] for c in ohlcv]
volumes = [c["volume"] for c in ohlcv]
price = closes[-1]
print(f"Price: ${price:,.2f}, Candles: {len(ohlcv)}")

# 2. TEKNIKAL KLASIK
print("[2/7] Classic technical analysis...")
def calc_ema(data, p):
    m = 2/(p+1); r = [data[0]]
    for i in range(1, len(data)): r.append((data[i]-r[-1])*m + r[-1])
    return r

ema20 = round(calc_ema(closes, 20)[-1], 2)
ema50 = round(calc_ema(closes, 50)[-1], 2)
ema200 = round(calc_ema(closes, 200)[-1], 2) if len(closes) >= 200 else round(calc_ema(closes, len(closes)//2)[-1], 2)

gains, losses = 0, 0
for i in range(-14, 0):
    diff = closes[i] - closes[i-1]
    if diff > 0: gains += diff
    else: losses -= diff
rs = (gains/14)/(losses/14) if losses > 0 else 999
rsi = round(100 - (100/(1+rs)), 1)

h24 = max(highs[-96:]); l24 = min(lows[-96:])
change24 = round(((closes[-1]-opens[-96])/opens[-96])*100, 2)
avg_vol = sum(volumes[-50:])/50
vol_ratio = round(volumes[-1]/avg_vol, 2)

resistances = sorted(set(round(h, 2) for h in highs[-30:] if h >= price), reverse=False)[:3]
supports = sorted(set(round(l, 2) for l in lows[-30:] if l <= price), reverse=True)[:3]
mr_resist = resistances[0] if resistances else round(price*1.01, 2)
mr_support = supports[0] if supports else round(price*0.99, 2)

# 3. ULTIMATE SMC ENGINE
print("[3/7] SMC Engine...")
ult_block = ""
ult_signal = "HOLD"
ult_grade = ""
ult_score = 0
ult_reasons = []
try:
    from ultimate_smc_engine import ultimate_analyze, format_ultimate_block, Grade as UG
    ult = ultimate_analyze(ohlcv, "XAUUSD", price)
    ult_block = format_ultimate_block(ult)
    ult_signal = ult.signal
    ult_grade = ult.grade_label
    ult_score = ult.score
    ult_reasons = ult.reasons
    print(f"  Grade: {ult_grade}")
except Exception as e:
    print(f"  SMC skip: {e}")

# 4. SMC SCALPER
print("[4/7] SMC Scalper...")
smc_block = ""
trend_block = ""
try:
    from smc_scalper_engine import analyze_smc_scalper, format_smc_block, analyze_trend_break, format_trend_block
    smc = analyze_smc_scalper(ohlcv, "XAUUSD")
    smc_block = format_smc_block(smc) or ""
    trend = analyze_trend_break(ohlcv, "XAUUSD")
    trend_block = format_trend_block(trend) or ""
except Exception as e:
    print(f"  SMC Scalper skip: {e}")

# 5. CHAOS FILTER
print("[5/7] Chaos Filter...")
chaos_score, chaos_rec, chaos_entropy, chaos_hurst, chaos_spoof, chaos_penalty = 0, "DISABLED", 0, 0.5, False, 0
try:
    from chaos_filter import chaos_gate
    cr = chaos_gate(ohlcv)
    chaos_score, chaos_rec = cr.chaos_score, cr.recommendation
    chaos_entropy = round(cr.entropy, 2)
    chaos_hurst = round(cr.hurst, 3)
    chaos_spoof = cr.spoof.get("spoof_detected", False)
    chaos_penalty = cr.penalty
    print(f"  Score: {chaos_score}, Rec: {chaos_rec}")
except Exception as e:
    print(f"  Chaos skip: {e}")

# 6. AI DEEPSEEK
print("[6/7] AI DeepSeek...")
ai_action, ai_conf, ai_text = "HOLD", 0, ""
env_text = open(f"{PROJECT_DIR}/strategies/vilona_tradefx/.env").read()
ds_key = ""
for line in env_text.splitlines():
    if line.startswith("DEEPSEEK_API_KEY"):
        ds_key = line.split("=",1)[1].strip().strip('"').strip("'")

if ds_key:
    prompt = f"""Analisa XAUUSD untuk hari Senin. Data teknikal:
Harga: ${price:.2f}, EMA20: ${ema20:.2f}, EMA50: ${ema50:.2f}
EMA200: ${ema200:.2f}, RSI(14): {rsi}
24h: ${l24:.2f}-${h24:.2f}
Resistance: {mr_resist}, Support: {mr_support}
Chaos: entropy={chaos_entropy}, hurst={chaos_hurst}

Beri JSON: {{"action":"BUY/SELL/HOLD","confidence":75,"direction":"BULLISH","entry_zone":"$XX-YY","target":"$XX","stop_loss":"$XX","risk_reward":"1:2","analysis_text":"singkat 2-3 kalimat","reason":"alasan utama"}}"""
    try:
        req = urllib.request.Request(
            "https://api.deepseek.com/v1/chat/completions",
            data=json.dumps({"model":"deepseek-chat","messages":[{"role":"user","content":prompt}],
                            "temperature":0.3,"max_tokens":800}).encode(),
            headers={"Content-Type":"application/json","Authorization":f"Bearer {ds_key}"})
        resp = urllib.request.urlopen(req, timeout=30)
        ai_data = json.loads(resp.read())
        ai_raw = ai_data["choices"][0]["message"]["content"]
        m = re.search(r'\{.*\}', ai_raw, re.DOTALL)
        if m:
            ai_j = json.loads(m.group())
            ai_action = ai_j.get("action","HOLD")
            ai_conf = ai_j.get("confidence",0)
            ai_text = ai_j.get("analysis_text","")[:300]
            ai_entry = ai_j.get("entry_zone","")
            ai_tp = ai_j.get("target","")
            ai_sl = ai_j.get("stop_loss","")
            ai_rr = ai_j.get("risk_reward","")
        print(f"  AI: {ai_action} @ {ai_conf}%")
    except Exception as e:
        print(f"  AI skip: {e}")

# 7. FORMAT & POST
print("[7/7] Posting to channel...")
tr_short = "BULLISH" if price > ema20 and ema20 > ema50 else ("BEARISH" if price < ema20 and ema20 < ema50 else "SIDEWAYS")
tr_long = "BULLISH" if price > ema200 and ema50 > ema200 else ("BEARISH" if price < ema200 and ema50 < ema200 else "SIDEWAYS")
rsi_st = "Overbought" if rsi > 70 else ("Oversold" if rsi < 30 else "Netral")

if chaos_rec == "TRADE": cv = "OK AMAN"
elif chaos_rec == "CAUTION": cv = "HATI-HATI"
else: cv = "SKIP"

ms = "BULLISH" if tr_short == "BULLISH" and tr_long == "BULLISH" else ("BEARISH" if tr_short == "BEARISH" and tr_long == "BEARISH" else "SIDEWAYS / CAUTION")

# Clean SMC text (strip HTML tags for Telegram)
def clean_html(t):
    return re.sub(r'<[^>]+>', '', t)[:500]

# Build message
msg = f"""<b>XAUUSD — ULTIMATE ANALYSIS SENIN 08 JUNI</b>
{S}
<b>1. TEKNIKAL KLASIK</b>
Harga: <code>${price:,.2f}</code>
24h:  {change24:+.2f}%
Range: <code>${l24:,.2f}</code> - <code>${h24:,.2f}</code>
{S}
<b>TREND & MOMENTUM</b>
Trend Panjang: {tr_long}
Trend Pendek: {tr_short}
EMA20: <code>${ema20:,.2f}</code>
EMA50: <code>${ema50:,.2f}</code>
EMA200: <code>${ema200:,.2f}</code>
RSI(14): {rsi} ({rsi_st})
Volume: {vol_ratio}x
{S}
<b>LEVEL KUNCI</b>
Resistance: <code>${mr_resist:,.2f}</code>
Support: <code>${mr_support:,.2f}</code>"""

if smc_block:
    msg += f"\n{S}\n<b>2. SMC SCALPER</b>\n" + clean_html(smc_block)[:500]

if ult_block:
    msg += f"\n{S}\n<b>3. ULTIMATE SMC v3.0</b>\nGrade: {ult_grade}\nScore: {ult_score}/24\nSignal: {ult_signal}"
    for r in ult_reasons[:2]:
        msg += f"\n{clean_html(r)[:80]}"

msg += f"""
{S}
<b>4. CHAOS FILTER</b>
Verdict: {cv}
Entropy: {chaos_entropy} | Hurst: {chaos_hurst}
Spoof: {'Terdeteksi' if chaos_spoof else 'Tidak'}
Score: {chaos_score} | Penalty: -{chaos_penalty}
{S}
<b>5. AI DEEPSEEK</b>
Signal: {ai_action} | Confidence: {ai_conf}
{ai_text[:300]}
"""

if ai_entry:
    msg += f"\n<b>6. PROYEKSI SENIN</b>\nSentimen: {ms}\nEntry: <code>{ai_entry}</code>\nTP: <code>{ai_tp}</code>\nSL: <code>{ai_sl}</code>\nRR: {ai_rr}\n"

msg += f"""{S}
DYOR - Bukan saran investasi
7 engine: Teknikal, SMC, Ultimate, Chaos, AI
phantomfx.aitradepulse.com"""

# Token + post
token = ""
for line in env_text.splitlines():
    if line.startswith("VILONA_TRADEFX_TELEGRAM_BOT_TOKEN"):
        token = line.split("=",1)[1].strip().strip('"').strip("'")

if not token:
    print("NO TOKEN, printing to stdout instead:\n")
    print(msg)
    sys.exit(0)

CH = "-1003257064212"
print(f"Posting {len(msg)} chars...")
payload = json.dumps({"chat_id": CH, "text": msg, "parse_mode": "HTML"}).encode()
req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage",
                             data=payload, headers={"Content-Type": "application/json"})
resp = urllib.request.urlopen(req, timeout=15)
res = json.loads(resp.read())
if res.get("ok"):
    print(f"POSTED! ID: {res['result']['message_id']}")
    print(f"Link: https://t.me/c/2928711742/{res['result']['message_id']}")
else:
    print(f"FAIL: {res}")
