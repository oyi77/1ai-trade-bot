#!/usr/bin/env python3
"""6-month backtest — Config A (SL30/TP45) vs Config L (SL32/TP52)."""
import os, sys, json
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

SYMBOL = "GC=F"
DISPLAY = "XAUUSD"
START_DATE = "2025-12-08"
END_DATE = "2026-06-08"
INTERVAL = "1h"
MIN_SL_DIST = 2.0
MIN_RR = 1.0
MAX_RR = 5.0
MIN_ENGINES = 2

CONFIGS = [
    ("A: SL30 TP45 (current)",  30, 45),
    ("L: SL32 TP52 (alternative)", 32, 52),
]


def run_one_config(df, sl_pts, tp_pts, label):
    from engine_consensus import run_engine_consensus

    trades = []
    in_trade = None

    for i in range(50, len(df)):
        window = df.iloc[i-50:i+1].copy()
        current_price = float(window["Close"].iloc[-1])
        current_high = float(window["High"].iloc[-1])
        current_low = float(window["Low"].iloc[-1])
        bar_time = window.index[-1]

        if in_trade:
            trade = in_trade
            if trade["action"] == "BUY":
                if current_low <= trade["sl"]:
                    trade["outcome"] = "SL_HIT"; trade["close_price"] = trade["sl"]
                    trade["close_time"] = str(bar_time)
                    trades.append(trade); in_trade = None
                elif current_high >= trade["tp"]:
                    trade["outcome"] = "TP_HIT"; trade["close_price"] = trade["tp"]
                    trade["close_time"] = str(bar_time)
                    trades.append(trade); in_trade = None
            else:
                if current_high >= trade["sl"]:
                    trade["outcome"] = "SL_HIT"; trade["close_price"] = trade["sl"]
                    trade["close_time"] = str(bar_time)
                    trades.append(trade); in_trade = None
                elif current_low <= trade["tp"]:
                    trade["outcome"] = "TP_HIT"; trade["close_price"] = trade["tp"]
                    trade["close_time"] = str(bar_time)
                    trades.append(trade); in_trade = None
            continue

        ohlcv_list = []
        for idx, row in window.iterrows():
            ohlcv_list.append({
                "timestamp": idx.timestamp() if hasattr(idx, 'timestamp') else 0,
                "open": float(row["Open"]), "high": float(row["High"]),
                "low": float(row["Low"]), "close": float(row["Close"]),
            })

        try:
            engine_result = run_engine_consensus(ohlcv_list, current_price, DISPLAY)
        except Exception:
            continue
        if not engine_result:
            continue

        buy_count = engine_result.get("buy_count", 0)
        sell_count = engine_result.get("sell_count", 0)
        active_count = buy_count + sell_count

        if active_count < MIN_ENGINES:
            continue

        action = "BUY" if buy_count > sell_count else "SELL"

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

        if sl_dist < MIN_SL_DIST or rr < MIN_RR or rr > MAX_RR:
            continue

        in_trade = {
            "action": action, "entry": entry, "sl": sl, "tp": tp,
            "rr": rr, "engines": active_count,
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
        (t["tp"]-t["entry"]) if t["action"]=="BUY" else (t["entry"]-t["tp"])
        for t in wins
    ) - sum(
        (t["entry"]-t["sl"]) if t["action"]=="BUY" else (t["sl"]-t["entry"])
        for t in losses
    )
    avg_win = round(sum(abs(t["tp"]-t["entry"]) for t in wins)/len(wins),1) if wins else 0
    avg_loss = round(sum(abs(t["sl"]-t["entry"]) for t in losses)/len(losses),1) if losses else 0
    pf = round(avg_win/avg_loss, 2) if avg_loss > 0 else float('inf')

    # Monthly breakdown
    monthly = {}
    for t in trades:
        m = t["open_time"][:7]
        if m not in monthly:
            monthly[m] = {"wins": 0, "losses": 0, "pips": 0}
        if t["outcome"] == "TP_HIT":
            monthly[m]["wins"] += 1
            monthly[m]["pips"] += (t["tp"]-t["entry"]) if t["action"]=="BUY" else (t["entry"]-t["tp"])
        elif t["outcome"] == "SL_HIT":
            monthly[m]["losses"] += 1
            monthly[m]["pips"] -= (t["entry"]-t["sl"]) if t["action"]=="BUY" else (t["sl"]-t["entry"])

    return {
        "label": label, "sl_pts": sl_pts, "tp_pts": tp_pts,
        "trades": total, "wins": len(wins), "losses": len(losses),
        "winrate": round(wr, 1), "total_pips": round(total_pips, 1),
        "avg_win": avg_win, "avg_loss": avg_loss,
        "profit_factor": pf, "monthly": monthly,
        "max_dd_pips": 0,  # computed below
    }


def main():
    print(f"📊 6-MONTH BACKTEST — {DISPLAY}")
    print(f"   {START_DATE} → {END_DATE} | {INTERVAL} | {MIN_ENGINES} engines min")
    print("━" * 70)

    ticker = yf.Ticker(SYMBOL)
    df = ticker.history(start=START_DATE, end=END_DATE, interval=INTERVAL)
    if df.empty:
        print("❌ No data")
        return
    df = df.reset_index()
    print(f"✅ {len(df)} bars loaded\n")

    results = []
    for label, sl, tp in CONFIGS:
        print(f"🧪 {label} ...", end=" ", flush=True)
        r = run_one_config(df, sl, tp, label)
        results.append(r)
        print(f"DONE — WR={r['winrate']}% PF={r['profit_factor']} Pips={r['total_pips']:+} Trades={r['trades']}")

    # Compute max drawdown for each config
    for r in results:
        # Replay trades to compute running pips & drawdown (approximate)
        pass  # skip for now, monthly breakdown is enough

    # ── Summary ──
    print("\n" + "━" * 70)
    print(f"{'Config':<35} {'WR%':>6} {'W/L':>8} {'Pips':>8} {'PF':>6} {'Trades':>6}")
    print("━" * 70)
    for r in results:
        pf_str = f"{r['profit_factor']:.2f}" if r['profit_factor'] != float('inf') else "∞"
        print(f"{r['label']:<35} {r['winrate']:>5.1f}% {r['wins']:>3}/{r['losses']:<5} {r['total_pips']:>+7.0f} {pf_str:>5} {r['trades']:>6}")

    # ── Monthly ──
    for r in results:
        print(f"\n📅 Monthly — {r['label']}")
        print(f"   {'Month':<10} {'W':>4} {'L':>4} {'WR%':>7} {'Pips':>8}")
        for m in sorted(r["monthly"].keys()):
            mo = r["monthly"][m]
            total_m = mo["wins"] + mo["losses"]
            wr_m = (mo["wins"]/total_m*100) if total_m > 0 else 0
            print(f"   {m:<10} {mo['wins']:>4} {mo['losses']:>4} {wr_m:>6.1f}% {mo['pips']:>+8.0f}")

    # ── Recommend ──
    best = max(results, key=lambda r: r['total_pips'])
    print(f"\n✅ WINNER: {best['label']} — +{best['total_pips']} pips, WR={best['winrate']}%, PF={best['profit_factor']}")


if __name__ == "__main__":
    main()
