#!/usr/bin/env python3
"""XAUUSD REAL Gold analysis - reads pre-fetched data"""
import json, re, os, sys, time, urllib.request, math

PROJECT_DIR = "/home/openclaw/projects/1ai-trade-bot"
os.chdir(PROJECT_DIR)
sys.path.insert(0, PROJECT_DIR)
SEP = "\u2501" * 22

# Read pre-fetched data
data = json.load(open("/tmp/xauusd_data.json"))
ohlcv = data["ohlcv"]
closes = data["closes"]
price = data["price"]

# Full arrays
highs = [c["high"] for c in ohlcv]
lows = [c["low"] for c in ohlcv]
opens = [c["open"] for c in ohlcv]
volumes = [c.get("volume", c.get("v", 0)) for c in ohlcv]

print(f"XAUUSD REAL (GC=F): {len(ohlcv)} bars, ${price:.2f}")

# ── TEKNIKAL ──
def calc_ema(d, p):
    m = 2/(p+1); r = [d[0]]
    for i in range(1, len(d)): r.append((d[i]-r[-1])*m + r[-1])
    return r

ema20 = round(calc_ema(closes, 20)[-1], 2)
ema50 = round(calc_ema(closes, 50)[-1], 2)
ema200 = round(calc_ema(closes, 200)[-1], 2) if len(closes) >= 200 else round(calc_ema(closes, len(closes)//2)[-1], 2)

g, l = 0, 0
for i in range(-14, 0):
    d = closes[i] - closes[i-1]
    if d > 0: g += d
    else: l -= d
rs = (g/14)/(l/14) if l > 0 else 999
rsi = round(100 - (100/(1+rs)), 1)

h24 = max(highs[-96:]); l24 = min(lows[-96:])
ch24 = round(((closes[-1]-opens[-96])/opens[-96])*100, 2)
av = sum(volumes[-50:])/50 if volumes else 1
vr = round(volumes[-1]/av, 2) if volumes else 0

resist = sorted(set(round(h, 2) for h in highs[-30:] if h >= price))[:3]
supp = sorted(set(round(l, 2) for l in lows[-30:] if l <= price), reverse=True)[:3]
mr_r = resist[0] if resist else round(price*1.01, 2)
mr_s = supp[0] if supp else round(price*0.99, 2)

print(f"EMA20/50/200: {ema20:.0f}/{ema50:.0f}/{ema200:.0f}")
print(f"RSI: {rsi}, 24h: {ch24:+.2f}%")

# ── ULTIMATE SMC ──
ult_grade, ult_score, ult_sig, ult_reas = "", 0, "HOLD", []
try:
    from ultimate_smc_engine import ultimate_analyze, Grade
    u = ultimate_analyze(ohlcv, "XAUUSD", price)
    ult_grade = u.grade_label
    ult_score = u.score
    ult_sig = u.signal
    ult_reas = u.reasons
    print(f"SMC: {ult_grade} ({ult_score}/24)")
except Exception as e:
    print(f"SMC: {e}")

# ── SMC SCALPER ──
smc_txt = ""
try:
    from smc_scalper_engine import analyze_smc_scalper, format_smc_block, analyze_trend_break, format_trend_block
    s = analyze_smc_scalper(ohlcv, "XAUUSD")
    smc_txt = format_smc_block(s) or ""
    tr = analyze_trend_break(ohlcv, "XAUUSD")
    smc_txt += "\n" + (format_trend_block(tr) or "")
except: pass

# ── CHAOS ──
cs, crec, cent, chur, cspoof, cpen = 0, "DISABLED", 0, 0.5, False, 0
try:
    from chaos_filter import chaos_gate
    cr = chaos_gate(ohlcv)
    cs, crec = cr.chaos_score, cr.recommendation
    cent = round(float(cr.entropy), 2) if isinstance(cr.entropy, (int, float)) else 0
    chur = round(float(cr.hurst), 3) if isinstance(cr.hurst, (int, float)) else 0.5
    cspoof = cr.spoof.get("spoof_detected", False) if hasattr(cr, 'spoof') else False
    cpen = cr.penalty
    print(f"Chaos: {cs}/{crec}")
except Exception as e:
    print(f"Chaos: {e}")

# ── AI ──
ai_a, ai_c, ai_t, ai_e, ai_tp, ai_sl, ai_rr = "HOLD", 0, "", "", "", "", ""
env = open("strategies/vilona_tradefx/.env").read()
dk = ""
for line in env.splitlines():
    if line.startswith("DEEPSEEK_API_KEY"):
        dk = line.split("=",1)[1].strip().strip('"').strip("'")
tk = ""
for line in env.splitlines():
    if line.startswith("VILONA_TRADEFX_TELEGRAM_BOT_TOKEN"):
        tk = line.split("=",1)[1].strip().strip('"').strip("'")

if dk:
    prompt = f"""XAUUSD GOLD analysis. REAL Gold data:
Price: ${price:.2f}, EMA20: ${ema20:.0f}, EMA50: ${ema50:.0f}, EMA200: ${ema200:.0f}
RSI(14): {rsi}, 24h: {ch24:+.2f}%
Range24h: ${l24:.0f}-${h24:.0f}
Resistance: ${mr_r:.0f}, Support: ${mr_s:.0f}

JSON: {{"action":"BUY/SELL/HOLD","confidence":0-100,"direction":"BULLISH/BEARISH","entry_zone":"$X-$Y","target":"$X","stop_loss":"$X","risk_reward":"1:X","analysis_text":"max 2 sentences","reason":"key reason"}}"""
    try:
        req = urllib.request.Request(
            "https://api.deepseek.com/v1/chat/completions",
            data=json.dumps({"model":"deepseek-chat","messages":[{"role":"user","content":prompt}],
                            "temperature":0.3,"max_tokens":500}).encode(),
            headers={"Content-Type":"application/json","Authorization":f"Bearer {dk}"})
        resp = urllib.request.urlopen(req, timeout=30)
        ad = json.loads(resp.read())
        ar = ad["choices"][0]["message"]["content"]
        m = re.search(r'\{.*\}', ar, re.DOTALL)
        if m:
            aj = json.loads(m.group())
            ai_a, ai_c = aj.get("action","HOLD"), aj.get("confidence",0)
            ai_t = aj.get("analysis_text","")[:250]
            ai_e, ai_tp = aj.get("entry_zone",""), aj.get("target","")
            ai_sl, ai_rr = aj.get("stop_loss",""), aj.get("risk_reward","")
        print(f"AI: {ai_a} {ai_c}%")
    except Exception as e:
        print(f"AI: {e}")

# ── FORMAT ──
tr_s = "BULLISH" if price > ema20 and ema20 > ema50 else ("BEARISH" if price < ema20 and ema20 < ema50 else "SIDEWAYS")
tr_l = "BULLISH" if price > ema200 and ema50 > ema200 else ("BEARISH" if price < ema200 and ema50 < ema200 else "SIDEWAYS")
rsi_st = "Jenuh Beli" if rsi > 70 else ("Jenuh Jual" if rsi < 30 else "Netral")
cv = "OK AMAN" if crec == "TRADE" else ("HATI-HATI" if crec == "CAUTION" else "SKIP")

msg = f"""<b>XAUUSD GOLD — ULTIMATE ANALYSIS SENIN 08 JUNI</b>
{SEP}
<b>1. TEKNIKAL KLASIK</b>
Harga: <code>${price:,.2f}</code>
24h: {ch24:+.2f}%
Range: <code>${l24:,.2f}</code> — <code>${h24:,.2f}</code>
{SEP}
<b>TREND & MOMENTUM</b>
Trend Panjang: {tr_l}
Trend Pendek: {tr_s}
EMA20: <code>${ema20:,.2f}</code>
EMA50: <code>${ema50:,.2f}</code>
EMA200: <code>${ema200:,.2f}</code>
RSI(14): {rsi} ({rsi_st})
Volume: {vr}x
{SEP}
<b>LEVEL KUNCI</b>
Resistance: <code>${mr_r:,.2f}</code>
Support: <code>${mr_s:,.2f}</code>"""

if smc_txt:
    sc = re.sub(r'<[^>]+>', '', smc_txt)[:500]
    msg += f"\n{SEP}\n<b>2. SMC SCALPER</b>\n{sc}"

msg += f"\n{SEP}\n<b>3. ULTIMATE SMC v3.0</b>\nGrade: {ult_grade}\nScore: {ult_score}/24\nSignal: {ult_sig}"
for r in ult_reas[:3]:
    msg += f"\n• {re.sub(r'<[^>]+>', '', r)[:80]}"

msg += f"""
{SEP}
<b>4. CHAOS FILTER</b>
Verdict: {cv}
Entropy: {cent} | Hurst: {chur}
Spoof: {"YA" if cspoof else "Tidak"}
Score: {cs} | Penalty: -{cpen}
{SEP}
<b>5. AI (DeepSeek)</b>
Signal: {ai_a} | Confidence: {ai_c}%
{ai_t[:250]}"""

if ai_e:
    msg += f"\n{SEP}\n<b>6. PROYEKSI SENIN</b>\nEntry: <code>{ai_e}</code>\nTP: <code>{ai_tp}</code>\nSL: <code>{ai_sl}</code>\nRR: {ai_rr}"

msg += f"\n{SEP}\n⚠️ DYOR — Bukan saran investasi\nData: GC=F Gold Futures\n7 engine: Teknikal, SMC, Ultimate, Chaos, AI\n🏠 phantomfx.aitradepulse.com"

print(f"\n=== Post ({len(msg)} chars) ===")
pay = json.dumps({"chat_id": "-1003257064212", "text": msg, "parse_mode": "HTML"}).encode()
req = urllib.request.Request(f"https://api.telegram.org/bot{tk}/sendMessage",
                             data=pay, headers={"Content-Type": "application/json"})
resp = urllib.request.urlopen(req, timeout=15)
res = json.loads(resp.read())
if res.get("ok"):
    print(f"✅ POSTED! ID: {res['result']['message_id']}")
else:
    print(f"❌ FAIL: {res}")
