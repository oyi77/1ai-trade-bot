#!/usr/bin/env python3
"""Grid backtest — test multiple SL/TP + min_engine combos in one run."""
import os, sys, json, time
from datetime import datetime
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
import pandas as pd
import numpy as np

SYMBOL = "GC=F"
DISPLAY = "XAUUSD"
START_DATE = "2026-03-08"
END_DATE = "2026-06-08"
INTERVAL = "1h"

# ── Grid Configs to test ──
CONFIGS = [
    # (name, min_engines, sl_pts, tp_pts)
    ("A: 2eng SL30 TP45 (current)", 2, 30, 45),
    ("B: 3eng SL30 TP45 (tighter)", 3, 30, 45),
    ("C: 2eng SL25 TP50 (RR 1:2)",   2, 25, 50),
    ("D: 3eng SL25 TP50 (RR 1:2)",   3, 25, 50),
    ("E: 2eng SL35 TP35 (RR 1:1)",   2, 35, 35),
    ("F: 3eng SL35 TP35 (RR 1:1)",   3, 35, 35),
    ("G: 2eng SL20 TP40 (RR 1:2)",   2, 20, 40),
    ("H: 3eng SL40 TP60 (RR 1:1.5)", 3, 40, 60),
]

MIN_SL_DIST = 2.0   # $2 minimum SL distance
MIN_RR = 1.0
MAX_RR = 5.0

def run_one_config(df, min_engines, sl_pts, tp_pts, label):
    from engine_consensus import run_engine_consensus

    trades = []
    in_trade = None
    total_signals = 0

    for i in range(50, len(df)):
        window = df.iloc[i-50:i+1].copy()
        current_price = float(window["Close"].iloc[-1])
        current_high = float(window["High"].iloc[-1])
        current_low = float(window["Low"].iloc[-1])
        bar_time = window.index[-1]

        # Check open trade
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
            else:
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

        ohlcv_list = []
        for idx, row in window.iterrows():
            ohlcv_list.append({
                "timestamp": idx.timestamp() if hasattr(idx, 'timestamp') else 0,
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
            })

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

        if active_count < min_engines:
            continue

        if buy_count > sell_count:
            action = "BUY"
        else:
            action = "SELL"

        total_signals += 1

        if action == "BUY":
            entry = current_price
            sl = round(entry - sl_pts, 2)
            tp = round(entry + tp_pts, 2)
        else:
            entry = current_price
            sl = round(entry + sl_pts, 2)
            tp = round(entry - tp_pts, 2)

        sl_dist = abs(entry - sl)
        tp_dist = abs(tp - entry)
        rr = round(tp_dist / sl_dist, 2) if sl_dist > 0 else 0

        if sl_dist < MIN_SL_DIST:
            continue
        if rr < MIN_RR or rr > MAX_RR:
            continue

        in_trade = {
            "action": action, "entry": entry, "sl": sl, "tp": tp,
            "rr": rr, "engines": active_count,
            "buy_engines": buy_count, "sell_engines": sell_count,
            "open_time": str(bar_time), "outcome": None,
            "close_price": None, "close_time": None,
        }

    if in_trade:
        in_trade["outcome"] = "OPEN"
        in_trade["close_price"] = float(df["Close"].iloc[-1])
        in_trade["close_time"] = str(df.index[-1])
        trades.append(in_trade)

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
    pf = round(avg_win/avg_loss, 2) if avg_loss > 0 else float('inf')

    return {
        "label": label,
        "min_engines": min_engines, "sl_pts": sl_pts, "tp_pts": tp_pts,
        "signals": total_signals, "trades": total,
        "wins": len(wins), "losses": len(losses),
        "winrate": round(wr, 1),
        "total_pips": round(total_pips, 1),
        "avg_win": avg_win, "avg_loss": avg_loss,
        "profit_factor": pf,
    }


def main():
    print(f"📊 GRID BACKTEST — {DISPLAY}\n")
    print(f"   Data: {START_DATE} → {END_DATE} ({INTERVAL})")
    print(f"   Total configs: {len(CONFIGS)}")
    print("━" * 70)

    # Fetch data once
    ticker = yf.Ticker(SYMBOL)
    df = ticker.history(start=START_DATE, end=END_DATE, interval=INTERVAL)
    if df.empty:
        print("❌ No data")
        return
    df = df.reset_index()
    print(f"✅ {len(df)} bars loaded\n")

    results = []
    for i, (label, min_eng, sl, tp) in enumerate(CONFIGS):
        print(f"🧪 [{i+1}/{len(CONFIGS)}] {label} ...", end=" ", flush=True)
        r = run_one_config(df, min_eng, sl, tp, label)
        results.append(r)
        print(f"WR={r['winrate']}% PF={r['profit_factor']} Pips={r['total_pips']:+}")

    # ── Summary Table ──
    print("\n" + "━" * 70)
    print(f"{'Config':<35} {'WR%':>6} {'W/L':>8} {'Pips':>8} {'PF':>6} {'Trades':>6}")
    print("━" * 70)

    best_pf = max(results, key=lambda r: r['profit_factor'] if r['profit_factor'] != float('inf') else 0)
    best_wr = max(results, key=lambda r: r['winrate'])
    best_pips = max(results, key=lambda r: r['total_pips'])

    for r in results:
        pf_str = f"{r['profit_factor']:.2f}" if r['profit_factor'] != float('inf') else "∞"
        marker = ""
        if r is best_pf: marker += " 💰"
        if r is best_wr: marker += " 🎯"
        if r is best_pips: marker += " 📈"
        print(f"{r['label']:<35} {r['winrate']:>5.1f}% {r['wins']}/{r['losses']:>5} {r['total_pips']:>+7.0f} {pf_str:>5} {r['trades']:>6}{marker}")

    print("━" * 70)
    print(f"💰 = Best Profit Factor | 🎯 = Best Winrate | 📈 = Best Pips")
    print(f"\n✅ RECOMMENDED: {best_pf['label']} — PF={best_pf['profit_factor']}, WR={best_pf['winrate']}%, +{best_pf['total_pips']} pips")

    # Save
    out_path = Path(__file__).resolve().parent.parent / "data" / "vilona_tradefx" / "backtest_grid.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2, default=str))
    print(f"\n📁 {out_path}")


if __name__ == "__main__":
    main()
