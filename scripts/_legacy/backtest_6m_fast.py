#!/usr/bin/env python3
"""
6-month backtest dengan data 1h dari yfinance — bypass M15 fetch.
Menggunakan data pre-loaded, bukan fetch per bar.
Config A (SL30 TP45) vs Config L (SL32 TP52).
"""
import os, sys, json, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Load env
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
import pandas as pd

SYMBOL = "GC=F"
DISPLAY = "XAUUSD"
START_DATE = "2025-12-08"
END_DATE = "2026-06-08"
MIN_ENGINES = 2
MIN_RR = 1.0
MAX_RR = 5.0
MIN_SL_DIST = 2.0

CONFIGS = [
    ("A: SL30 TP45 (current)", 30, 45),
    ("L: SL32 TP52 (alternative)", 32, 52),
]

# ── Pre-load ALL data once ──
print(f"📊 Loading {DISPLAY} 1h data {START_DATE} → {END_DATE}...")
ticker = yf.Ticker(SYMBOL)
df = ticker.history(start=START_DATE, end=END_DATE, interval="1h")
print(f"✅ {len(df)} bars loaded")
df = df.reset_index()

# ── Build OHLCV cache ──
all_ohlcv = []
for _, row in df.iterrows():
    all_ohlcv.append({
        "timestamp": row["Datetime"].timestamp(),
        "open": float(row["Open"]),
        "high": float(row["High"]),
        "low": float(row["Low"]),
        "close": float(row["Close"]),
    })

print(f"✅ OHLCV cache built: {len(all_ohlcv)} bars")

# ── Test cache hit rate ──
from engine_consensus import run_engine_consensus

test_idx = min(200, len(all_ohlcv) - 1)
test_window = all_ohlcv[:test_idx+1]
test_price = all_ohlcv[test_idx]["close"]
print(f"\n🧪 Testing consensus with {len(test_window)} bars...")
t0 = time.time()
result = run_engine_consensus(test_window, test_price, DISPLAY)
elapsed = time.time() - t0
print(f"   Verdict: {result.get('verdict')} | Consensus: {result.get('consensus_pct',0)*100:.0f}% | Time: {elapsed:.1f}s")

if elapsed > 30:
    print("\n⚠️ Too slow! Will take hours for 2785 bars. Using cached approach instead.")
    sys.exit(1)

print("\n✅ Speed OK. Running full backtest...")
# ── Patch engine_consensus to cache M15/M5 data ──
import engine_consensus
# Set M15/M5 cache TTL to 1 hour so they cache after first fetch
engine_consensus.TF_CACHE_TTL["M15"] = 3600
engine_consensus.TF_CACHE_TTL["M5"] = 3600
print("✅ Patched engine_consensus cache TTL (M15/M5 → 3600s)")

# Pre-fetch M15 data to warm up cache
print("📡 Pre-fetching M15 data to warm cache...")
engine_consensus._fetch_yf_bars(DISPLAY, "M15")
engine_consensus._fetch_yf_bars(DISPLAY, "M5")
print("✅ Cache warmed")

print("━" * 70)

# ── Run backtest ──
for label, sl_pts, tp_pts in CONFIGS:
    print(f"\n🧪 Testing {label}...")
    trades = []
    in_trade = None
    total_signals = 0

    for i in range(100, len(all_ohlcv)):
        window = all_ohlcv[:i+1]
        current_price = window[-1]["close"]

        # Check open trade
        if in_trade:
            trade = in_trade
            bar_time = df.iloc[i]["Datetime"]
            if trade["action"] == "BUY":
                if current_price <= trade["sl"]:
                    trade["outcome"] = "SL_HIT"
                    trade["close_price"] = trade["sl"]
                    trade["close_time"] = str(bar_time)
                    trades.append(trade)
                    in_trade = None
                elif current_price >= trade["tp"]:
                    trade["outcome"] = "TP_HIT"
                    trade["close_price"] = trade["tp"]
                    trade["close_time"] = str(bar_time)
                    trades.append(trade)
                    in_trade = None
            else:
                if current_price >= trade["sl"]:
                    trade["outcome"] = "SL_HIT"
                    trade["close_price"] = trade["sl"]
                    trade["close_time"] = str(bar_time)
                    trades.append(trade)
                    in_trade = None
                elif current_price <= trade["tp"]:
                    trade["outcome"] = "TP_HIT"
                    trade["close_price"] = trade["tp"]
                    trade["close_time"] = str(bar_time)
                    trades.append(trade)
                    in_trade = None
            continue

        # Generate signal (skip first 50 bars for engine warmup)
        if i < 50:
            continue

        try:
            engine_result = run_engine_consensus(window, current_price, DISPLAY)
        except Exception as e:
            continue
        if not engine_result:
            continue

        buy_count = engine_result.get("buy_count", 0)
        sell_count = engine_result.get("sell_count", 0)
        active_count = buy_count + sell_count

        if active_count < MIN_ENGINES:
            continue

        action = "BUY" if buy_count > sell_count else "SELL"
        total_signals += 1

        entry = current_price
        if action == "BUY":
            sl = round(entry - sl_pts, 2)
            tp = round(entry + tp_pts, 2)
        else:
            sl = round(entry + sl_pts, 2)
            tp = round(entry - tp_pts, 2)

        sl_dist = abs(entry - sl)
        tp_dist = abs(tp - entry)
        rr = round(tp_dist / sl_dist, 2) if sl_dist > 0 else 0

        if sl_dist < MIN_SL_DIST or rr < MIN_RR or rr > MAX_RR:
            continue

        in_trade = {
            "action": action, "entry": entry, "sl": sl, "tp": tp,
            "rr": rr, "engines": active_count,
            "buy_engines": buy_count, "sell_engines": sell_count,
            "open_time": str(df.iloc[i]["Datetime"]),
            "outcome": None, "close_price": None, "close_time": None,
        }

    # Close any open trade
    if in_trade:
        in_trade["outcome"] = "OPEN"
        in_trade["close_price"] = all_ohlcv[-1]["close"]
        in_trade["close_time"] = str(df.iloc[-1]["Datetime"])
        trades.append(in_trade)

    # Results
    wins = [t for t in trades if t["outcome"] == "TP_HIT"]
    losses = [t for t in trades if t["outcome"] == "SL_HIT"]
    total = len(trades)
    wr = (len(wins) / total * 100) if total > 0 else 0
    total_pips = sum(
        (t["tp"] - t["entry"]) if t["action"] == "BUY" else (t["entry"] - t["tp"])
        for t in wins
    ) - sum(
        (t["entry"] - t["sl"]) if t["action"] == "BUY" else (t["sl"] - t["entry"])
        for t in losses
    )
    avg_win = round(sum(abs(t["tp"]-t["entry"]) for t in wins)/len(wins),1) if wins else 0
    avg_loss = round(sum(abs(t["sl"]-t["entry"]) for t in losses)/len(losses),1) if losses else 0
    pf = round(total_pips / (avg_loss * len(losses)) if losses else 0, 2) if total_pips > 0 else 0

    print(f"   {label}")
    print(f"   Trades: {total} | Wins: {len(wins)} | Losses: {len(losses)}")
    print(f"   WR: {wr:.1f}% | Pips: {total_pips:+.1f} | PF: {pf}")
    print(f"   Avg Win: +{avg_win} | Avg Loss: -{avg_loss}")
    print()

# Save results
out = {
    "config": {"symbol": SYMBOL, "start": START_DATE, "end": END_DATE, "interval": "1h"},
    "results": {}
}
out_path = Path(__file__).resolve().parent.parent / "data" / "vilona_tradefx" / "backtest_6m_1h.json"
out_path.write_text(json.dumps(out, indent=2, default=str))
print(f"📁 Saved: {out_path}")
