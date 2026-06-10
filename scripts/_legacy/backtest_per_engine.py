#!/usr/bin/env python3
"""
Per-Engine Winrate Backtest — Track individual engine performance.
Each engine's vote (BUY/SELL/HOLD) is recorded per bar and compared
against actual trade outcome.
"""
import os, sys, json, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

env_path = Path(__file__).resolve().parent.parent / "strategies" / "vilona_tradefx" / ".env"
if env_path.exists():
    for line in env_path.read_text().split("\n"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip().strip('"').strip("'")

import warnings
warnings.filterwarnings("ignore")
import yfinance as yf
import numpy as np
import engine_consensus

# Patch cache for speed
engine_consensus.TF_CACHE_TTL["M15"] = 3600
engine_consensus.TF_CACHE_TTL["M5"] = 3600
engine_consensus._fetch_yf_bars("XAUUSD", "M15")
engine_consensus._fetch_yf_bars("XAUUSD", "M5")

SYMBOL = "GC=F"
DISPLAY = "XAUUSD"
START_DATE = "2026-03-08"
END_DATE = "2026-06-08"
SL_PTS = 30
TP_PTS = 45
MIN_ENGINES = 2
MIN_RR = 1.2
MAX_RR = 5.0
MIN_SL_DIST = 2.0

# Primary timeframe to track individual engine votes
PRIMARY_TF = "H1"

ENGINE_NAMES = {
    "quant": "Quant", "fvg": "FVG", "hermes": "Hermes",
    "crt": "CRT/TBS", "smc": "SMC", "trend": "Trend",
    "ultimate": "Ultimate", "sequoia": "Sequoia"
}

print(f"📊 PER-ENGINE BACKTEST — {DISPLAY}")
print(f"   {START_DATE} → {END_DATE} | 1h | Primary TF: {PRIMARY_TF}")
print(f"   Config: SL{SL_PTS}/TP{TP_PTS} | Min {MIN_ENGINES} engines")
print("━" * 70)

ticker = yf.Ticker(SYMBOL)
df = ticker.history(start=START_DATE, end=END_DATE, interval="1h")
print(f"✅ {len(df)} bars loaded")
df = df.reset_index()

# Build OHLCV once
all_ohlcv = []
for _, row in df.iterrows():
    all_ohlcv.append({
        "timestamp": row["Datetime"].timestamp(),
        "open": float(row["Open"]), "high": float(row["High"]),
        "low": float(row["Low"]), "close": float(row["Close"]),
    })

print(f"✅ OHLCV: {len(all_ohlcv)} bars")

# Per-engine tracking
engine_trades = {}  # engine_name -> list of {action, outcome, entry, close, pips}
total_signals = 0
consensus_trades = []

in_trade = None

for i in range(100, len(all_ohlcv)):
    window = all_ohlcv[:i+1]
    current_price = window[-1]["close"]

    # Check open trade
    if in_trade:
        trade = in_trade
        if trade["action"] == "BUY":
            if current_price <= trade["sl"]:
                trade["outcome"] = "SL_HIT"; trade["close_price"] = trade["sl"]
                trade["close_time"] = str(df.iloc[i]["Datetime"])
                consensus_trades.append(trade)
                # Record per-engine
                for ename, vote in trade.get("engine_votes", {}).items():
                    if vote in ("BUY", "SELL"):
                        is_correct = (vote == trade["action"])
                        en_key = ename
                        if en_key not in engine_trades:
                            engine_trades[en_key] = []
                        engine_trades[en_key].append({
                            "action": trade["action"], "outcome": "SL_HIT",
                            "correct": is_correct == (trade["outcome"] == "TP_HIT"),
                            "entry": trade["entry"], "close": trade["close_price"]
                        })
                in_trade = None
            elif current_price >= trade["tp"]:
                trade["outcome"] = "TP_HIT"; trade["close_price"] = trade["tp"]
                trade["close_time"] = str(df.iloc[i]["Datetime"])
                consensus_trades.append(trade)
                for ename, vote in trade.get("engine_votes", {}).items():
                    if vote in ("BUY", "SELL"):
                        en_key = ename
                        if en_key not in engine_trades:
                            engine_trades[en_key] = []
                        engine_trades[en_key].append({
                            "action": trade["action"], "outcome": "TP_HIT",
                            "correct": True,
                            "entry": trade["entry"], "close": trade["close_price"]
                        })
                in_trade = None
        else:  # SELL
            if current_price >= trade["sl"]:
                trade["outcome"] = "SL_HIT"; trade["close_price"] = trade["sl"]
                trade["close_time"] = str(df.iloc[i]["Datetime"])
                consensus_trades.append(trade)
                for ename, vote in trade.get("engine_votes", {}).items():
                    if vote in ("BUY", "SELL"):
                        en_key = ename
                        if en_key not in engine_trades:
                            engine_trades[en_key] = []
                        engine_trades[en_key].append({
                            "action": trade["action"], "outcome": "SL_HIT",
                            "correct": False,
                            "entry": trade["entry"], "close": trade["close_price"]
                        })
                in_trade = None
            elif current_price <= trade["tp"]:
                trade["outcome"] = "TP_HIT"; trade["close_price"] = trade["tp"]
                trade["close_time"] = str(df.iloc[i]["Datetime"])
                consensus_trades.append(trade)
                for ename, vote in trade.get("engine_votes", {}).items():
                    if vote in ("BUY", "SELL"):
                        en_key = ename
                        if en_key not in engine_trades:
                            engine_trades[en_key] = []
                        engine_trades[en_key].append({
                            "action": trade["action"], "outcome": "TP_HIT",
                            "correct": True,
                            "entry": trade["entry"], "close": trade["close_price"]
                        })
                in_trade = None
        continue

    if i < 50:
        continue

    # Get engine consensus
    try:
        result = engine_consensus.run_engine_consensus(window, current_price, DISPLAY)
    except Exception:
        continue
    if not result:
        continue

    # Get primary TF engine votes (H1)
    tf_data = result.get("timeframes", {}).get(PRIMARY_TF, {})
    engines = tf_data.get("engines", {}) or result.get("engines", {})

    buy_count = result.get("buy_count", 0)
    sell_count = result.get("sell_count", 0)
    active_count = buy_count + sell_count

    if active_count < MIN_ENGINES:
        continue

    # Consensus direction
    if buy_count > sell_count:
        action = "BUY"
    else:
        action = "SELL"
    total_signals += 1

    entry = current_price
    if action == "BUY":
        sl = round(entry - SL_PTS, 2)
        tp = round(entry + TP_PTS, 2)
    else:
        sl = round(entry + SL_PTS, 2)
        tp = round(entry - TP_PTS, 2)

    sl_dist = abs(entry - sl)
    tp_dist = abs(tp - entry)
    rr = round(tp_dist / sl_dist, 2) if sl_dist > 0 else 0
    if sl_dist < MIN_SL_DIST or rr < MIN_RR or rr > MAX_RR:
        continue

    # Record each engine's vote on this trade
    engine_votes = {}
    for ename, edata in engines.items():
        engine_votes[ename] = edata.get("direction", "HOLD")

    in_trade = {
        "action": action, "entry": entry, "sl": sl, "tp": tp,
        "rr": rr, "engines": active_count,
        "engine_votes": engine_votes,
        "open_time": str(df.iloc[i]["Datetime"]),
        "outcome": None, "close_price": None, "close_time": None,
    }

# Close open
if in_trade:
    in_trade["outcome"] = "OPEN"
    in_trade["close_price"] = all_ohlcv[-1]["close"]
    in_trade["close_time"] = str(df.iloc[-1]["Datetime"])
    consensus_trades.append(in_trade)

# ── Results ──
print("\n📊 CONSENSUS RESULTS")
print("━" * 70)
wins = [t for t in consensus_trades if t["outcome"] == "TP_HIT"]
losses = [t for t in consensus_trades if t["outcome"] == "SL_HIT"]
total = len(consensus_trades)
wr = (len(wins) / total * 100) if total > 0 else 0
total_pips = sum(
    (t["tp"]-t["entry"]) if t["action"]=="BUY" else (t["entry"]-t["tp"])
    for t in wins
) - sum(
    (t["entry"]-t["sl"]) if t["action"]=="BUY" else (t["sl"]-t["entry"])
    for t in losses
)
print(f"   Total Trades: {total} | Wins: {len(wins)} | Losses: {len(losses)}")
print(f"   Winrate: {wr:.1f}% | Total Pips: {total_pips:+.1f}")

# Per-engine results
print("\n🔬 PER-ENGINE WINRATE (Primary TF: " + PRIMARY_TF + ")")
print("━" * 70)
print(f"   {'Engine':<14} {'Trades':>7} {'Wins':>5} {'Loss':>5} {'WR%':>7} {'Active%':>8}")
print("━" * 70)

engine_summary = {}
for ename in ["quant", "fvg", "hermes", "crt", "smc", "trend", "ultimate", "sequoia"]:
    label = ENGINE_NAMES.get(ename, ename)
    trades = engine_trades.get(ename, [])
    if not trades:
        print(f"   {label:<14} {'N/A':>7} {'-':>5} {'-':>5} {'N/A':>7} {'0.0%':>8}")
        continue
    ewins = [t for t in trades if t["outcome"] == "TP_HIT"]
    eloss = [t for t in trades if t["outcome"] == "SL_HIT"]
    ewr = (len(ewins) / len(trades) * 100) if trades else 0
    active_pct = len(trades) / total * 100 if total > 0 else 0
    engine_summary[ename] = {
        "trades": len(trades), "wins": len(ewins), "losses": len(eloss),
        "winrate": round(ewr, 1), "active_pct": round(active_pct, 1)
    }
    color = "\033[32m" if ewr >= 50 else "\033[31m"
    reset = "\033[0m"
    print(f"   {label:<14} {len(trades):>7} {len(ewins):>5} {len(eloss):>5} {color}{ewr:>6.1f}%{reset} {active_pct:>7.1f}%")

print("━" * 70)
print(f"   {'Total Consensus':<14} {total:>7} {len(wins):>5} {len(losses):>5} {wr:>6.1f}% {'100.0%':>8}")

# AI Improvement Suggestions
print("\n🤖 AI IMPROVEMENT ANALYSIS")
print("━" * 70)

# Find best engines
best_engines = sorted(
    [(ename, s) for ename, s in engine_summary.items() if s["trades"] > 0],
    key=lambda x: x[1]["winrate"], reverse=True
)

if best_engines:
    best = best_engines[0]
    print(f"   🏆 Best Engine: {ENGINE_NAMES.get(best[0], best[0])} ({best[1]['winrate']}% WR)")
    print(f"   🔻 Worst Engine: {ENGINE_NAMES.get(best_engines[-1][0], best_engines[-1][0])} ({best_engines[-1][1]['winrate']}% WR)")

    # Simulate with top 3 engines only
    top3 = [e[0] for e in best_engines[:3]]
    print(f"\n   🎯 Top 3 Engines: {', '.join(ENGINE_NAMES.get(e,e) for e in top3)}")

    # Count how many trades top-3 would have agreed on
    top3_correct = 0
    top3_total = 0
    for t in consensus_trades:
        if t["outcome"] not in ("TP_HIT", "SL_HIT"):
            continue
        votes = t.get("engine_votes", {})
        active_top3 = [e for e in top3 if votes.get(e) in ("BUY", "SELL")]
        if len(active_top3) >= 2:
            dirs = [votes[e] for e in top3 if votes.get(e) in ("BUY", "SELL")]
            agree = all(d == dirs[0] for d in dirs)
            if agree:
                top3_total += 1
                if t["outcome"] == "TP_HIT":
                    top3_correct += 1

    if top3_total > 0:
        top3_wr = top3_correct / top3_total * 100
        print(f"   📈 If only Top 3 agree: {top3_total} trades, {top3_correct} wins = {top3_wr:.1f}% WR")
        print(f"   🔥 Improvement: {top3_wr - wr:+.1f}% vs consensus")

# Save results
out = {
    "config": {"symbol": SYMBOL, "start": START_DATE, "end": END_DATE,
               "sl": SL_PTS, "tp": TP_PTS, "primary_tf": PRIMARY_TF},
    "consensus": {
        "trades": total, "wins": len(wins), "losses": len(losses),
        "winrate": round(wr, 1), "total_pips": round(total_pips, 1)
    },
    "engines": engine_summary
}
out_path = Path(__file__).resolve().parent.parent / "data" / "vilona_tradefx" / "backtest_per_engine.json"
out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text(json.dumps(out, indent=2, default=str))
print(f"\n📁 Saved: {out_path}")
