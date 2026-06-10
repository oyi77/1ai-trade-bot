#!/usr/bin/env python3
"""Backtest XAUUSD strategy engines over historical data — no AI cost, engines-only.
Simulates: CRT, SMC, FVG, Trend Break, Quant, Hermes with new quality gate parameters."""
import os, sys, json, time
from datetime import datetime, timedelta
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
import pandas as pd
import numpy as np

# ── Config ──
SYMBOL = "GC=F"
DISPLAY = "XAUUSD"
START_DATE = "2026-03-08"
END_DATE = "2026-06-08"
INTERVAL = "1h"

# Quality gate (same as production now)
MIN_ENGINES = 2        # min engines must agree
MIN_RR = 1.2           # min RR
MAX_RR = 5.0           # max RR
MIN_SL_DIST = 2.0      # $2.00 = 20 pips for XAUUSD
SL_POINTS = 32         # Config L: default SL = 32 points ($3.20)
TP_POINTS = 52         # Config L: default TP = 52 points ($5.20) → RR 1:1.625

def run_backtest():
    print(f"📊 Backtest {DISPLAY} ({SYMBOL})")
    print(f"   Period: {START_DATE} → {END_DATE}")
    print(f"   Interval: {INTERVAL}")
    print(f"   Engines: CRT + SMC + FVG + Trend + Quant + Hermes")
    print(f"   Quality: min {MIN_ENGINES} engines, RR {MIN_RR}-{MAX_RR}, SL≥${MIN_SL_DIST}")
    print("━" * 50)

    # Fetch data
    ticker = yf.Ticker(SYMBOL)
    df = ticker.history(start=START_DATE, end=END_DATE, interval=INTERVAL)
    if df.empty:
        print("❌ No data fetched")
        return

    print(f"✅ {len(df)} bars loaded")
    df = df.reset_index()

    # Import engines (all through consensus only)
    from engine_consensus import run_engine_consensus

    trades = []
    in_trade = None
    total_signals = 0

    # Walk through bars (need 50 bars warmup for engines)
    for i in range(50, len(df)):
        window = df.iloc[i-50:i+1].copy()
        current_price = float(window["Close"].iloc[-1])
        current_high = float(window["High"].iloc[-1])
        current_low = float(window["Low"].iloc[-1])
        bar_time = window.index[-1]

        # Check open trade first
        if in_trade:
            trade = in_trade
            if trade["action"] == "BUY":
                if current_low <= trade["sl"]:
                    trade["outcome"] = "SL_HIT"
                    trade["close_price"] = trade["sl"]
                    trade["close_time"] = str(bar_time)
                    trades.append(trade)
                    in_trade = None
                elif current_high >= trade["tp"]:
                    trade["outcome"] = "TP_HIT"
                    trade["close_price"] = trade["tp"]
                    trade["close_time"] = str(bar_time)
                    trades.append(trade)
                    in_trade = None
            else:  # SELL
                if current_high >= trade["sl"]:
                    trade["outcome"] = "SL_HIT"
                    trade["close_price"] = trade["sl"]
                    trade["close_time"] = str(bar_time)
                    trades.append(trade)
                    in_trade = None
                elif current_low <= trade["tp"]:
                    trade["outcome"] = "TP_HIT"
                    trade["close_price"] = trade["tp"]
                    trade["close_time"] = str(bar_time)
                    trades.append(trade)
                    in_trade = None
            continue

        # Convert window DataFrame to list of dicts for engine consensus
        ohlcv_list = []
        for idx, row in window.iterrows():
            ohlcv_list.append({
                "timestamp": idx.timestamp() if hasattr(idx, 'timestamp') else 0,
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
            })

        # Generate signal from engines
        try:
            engine_result = run_engine_consensus(ohlcv_list, current_price, DISPLAY)
        except Exception:
            continue

        if not engine_result:
            continue

        engines = engine_result.get("engines", {})
        buy_count = engine_result.get("buy_count", 0)
        sell_count = engine_result.get("sell_count", 0)
        active_count = buy_count + sell_count

        if active_count < MIN_ENGINES:
            continue

        # Determine direction
        if buy_count > sell_count:
            action = "BUY"
        else:
            action = "SELL"

        total_signals += 1

        # Calculate SL/TP
        if action == "BUY":
            entry = current_price
            sl = round(entry - SL_POINTS, 2)
            tp = round(entry + TP_POINTS, 2)
        else:
            entry = current_price
            sl = round(entry + SL_POINTS, 2)
            tp = round(entry - TP_POINTS, 2)

        sl_dist = abs(entry - sl)
        tp_dist = abs(tp - entry)
        rr = round(tp_dist / sl_dist, 2) if sl_dist > 0 else 0

        # Quality gate
        if sl_dist < MIN_SL_DIST:
            continue
        if rr < MIN_RR or rr > MAX_RR:
            continue

        # Open trade
        in_trade = {
            "action": action,
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "rr": rr,
            "engines": active_count,
            "buy_engines": buy_count,
            "sell_engines": sell_count,
            "open_time": str(bar_time),
            "outcome": None,
            "close_price": None,
            "close_time": None,
        }

    # Close any remaining open trade at last price
    if in_trade:
        in_trade["outcome"] = "OPEN"
        in_trade["close_price"] = float(df["Close"].iloc[-1])
        in_trade["close_time"] = str(df.index[-1])
        trades.append(in_trade)

    # ── Results ──
    wins = [t for t in trades if t["outcome"] == "TP_HIT"]
    losses = [t for t in trades if t["outcome"] == "SL_HIT"]
    opens = [t for t in trades if t["outcome"] == "OPEN"]

    total = len(trades)
    wr = (len(wins) / total * 100) if total > 0 else 0
    total_pips = sum(
        (t["tp"] - t["entry"]) if t["action"] == "BUY" else (t["entry"] - t["tp"])
        for t in wins
    ) - sum(
        (t["entry"] - t["sl"]) if t["action"] == "BUY" else (t["sl"] - t["entry"])
        for t in losses
    )
    avg_win = round(sum(
        abs(t["tp"] - t["entry"]) for t in wins
    ) / len(wins), 1) if wins else 0
    avg_loss = round(sum(
        abs(t["sl"] - t["entry"]) for t in losses
    ) / len(losses), 1) if losses else 0

    print(f"\n📈 RESULTS — {DISPLAY} Backtest")
    print("━" * 50)
    print(f"   Period: {START_DATE} → {END_DATE} (3 months)")
    print(f"   Total signals: {total_signals} | Trades opened: {total}")
    print(f"   ✅ Wins: {len(wins)} | ❌ Losses: {len(losses)} | 📌 Open: {len(opens)}")
    print(f"   🎯 Winrate: {wr:.1f}%")
    print(f"   💰 Total Pips: {total_pips:+.1f}")
    print(f"   📐 Avg Win: +{avg_win} pts | Avg Loss: -{avg_loss} pts")
    print(f"   📊 Profit Factor: {round(avg_win / avg_loss, 2) if avg_loss > 0 else '∞'}")

    # Monthly breakdown
    print(f"\n📅 Monthly Breakdown:")
    for t in trades:
        dt = t["open_time"][:7]
        print(f"   {dt}: {t['action']} {t['outcome']} | E={t['entry']} SL={t['sl']} TP={t['tp']} | engines={t['engines']}")

    # Save to file
    out = {
        "config": {"symbol": SYMBOL, "start": START_DATE, "end": END_DATE,
                   "min_engines": MIN_ENGINES, "min_rr": MIN_RR, "max_rr": MAX_RR,
                   "min_sl": MIN_SL_DIST, "sl_pts": SL_POINTS, "tp_pts": TP_POINTS},
        "results": {
            "total_signals": total_signals, "trades": total,
            "wins": len(wins), "losses": len(losses), "open": len(opens),
            "winrate": round(wr, 1), "total_pips": round(total_pips, 1),
            "avg_win": avg_win, "avg_loss": avg_loss,
        },
        "trades": trades
    }
    out_path = Path(__file__).resolve().parent.parent / "data" / "vilona_tradefx" / "backtest_xauusd_3m.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2, default=str))
    print(f"\n📁 Saved: {out_path}")

    return out["results"]

if __name__ == "__main__":
    results = run_backtest()
