#!/usr/bin/env python3
"""Grid backtest — crypto BTC & ETH with multiple SL/TP configs."""
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

START_DATE = "2025-12-08"
END_DATE = "2026-06-08"
INTERVAL = "1h"
MIN_ENGINES = 2
MIN_RR = 1.0
MAX_RR = 5.0

# ── Crypto Pairs ──
CRYPTO = [
    ("BTC-USD", "BTCUSD", [
        # (label, sl_pts, tp_pts)
        ("BTC A: SL500 TP1000 (RR 1:2, current)", 500, 1000),
        ("BTC B: SL400 TP800 (RR 1:2)",            400, 800),
        ("BTC C: SL600 TP1200 (RR 1:2)",           600, 1200),
        ("BTC D: SL500 TP750 (RR 1:1.5)",          500, 750),
        ("BTC E: SL400 TP600 (RR 1:1.5)",          400, 600),
    ]),
    ("ETH-USD", "ETHUSD", [
        ("ETH A: SL50 TP100 (RR 1:2)",             50, 100),
        ("ETH B: SL40 TP80 (RR 1:2)",              40, 80),
        ("ETH C: SL60 TP120 (RR 1:2)",             60, 120),
        ("ETH D: SL50 TP75 (RR 1:1.5)",            50, 75),
        ("ETH E: SL40 TP60 (RR 1:1.5)",            40, 60),
    ]),
]


def run_one_config(df, sl_pts, tp_pts, min_sl_dist, label):
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
            engine_result = run_engine_consensus(ohlcv_list, current_price, "BTCUSD" if "BTC" in label else "ETHUSD")
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
        if sl_dist < min_sl_dist or rr < MIN_RR or rr > MAX_RR:
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
    wr = (len(wins)/total*100) if total > 0 else 0
    total_pips = sum((t["tp"]-t["entry"]) if t["action"]=="BUY" else (t["entry"]-t["tp"]) for t in wins) \
               - sum((t["entry"]-t["sl"]) if t["action"]=="BUY" else (t["sl"]-t["entry"]) for t in losses)
    avg_win = round(sum(abs(t["tp"]-t["entry"]) for t in wins)/len(wins),1) if wins else 0
    avg_loss = round(sum(abs(t["sl"]-t["entry"]) for t in losses)/len(losses),1) if losses else 0
    pf = round(avg_win/avg_loss,2) if avg_loss>0 else float('inf')

    return {
        "label": label, "trades": total, "wins": len(wins), "losses": len(losses),
        "winrate": round(wr,1), "total_pips": round(total_pips,1),
        "avg_win": avg_win, "avg_loss": avg_loss, "profit_factor": pf,
    }


def main():
    print(f"📊 CRYPTO BACKTEST — 6 Bulan ({START_DATE} → {END_DATE})")
    print("━" * 75)

    all_results = []
    for symbol, display, configs in CRYPTO:
        print(f"\n🔹 {display} ({symbol})")
        ticker = yf.Ticker(symbol)
        df = ticker.history(start=START_DATE, end=END_DATE, interval=INTERVAL)
        if df.empty:
            print(f"   ❌ No data")
            continue
        df = df.reset_index()
        print(f"   ✅ {len(df)} bars | Price range: ${df['Close'].min():.0f} – ${df['Close'].max():.0f}")

        # Min SL dist = 0.1% of avg price for crypto
        avg_price = float(df["Close"].mean())
        min_sl = round(avg_price * 0.001, 1)  # 0.1%

        results = []
        for label, sl, tp in configs:
            print(f"   🧪 {label:<42}", end=" ", flush=True)
            r = run_one_config(df, sl, tp, min_sl, label)
            results.append(r)
            print(f"WR={r['winrate']:>5.1f}% PF={r['profit_factor']:>5} T={r['trades']:>4} Pips={r['total_pips']:>+8.0f}")

        # Table
        print(f"\n   {'Config':<42} {'WR%':>6} {'W/L':>8} {'Pips':>10} {'PF':>6} {'Trades':>6}")
        print("   " + "─" * 75)
        best = max(results, key=lambda r: r['total_pips'])
        for r in results:
            pf_s = f"{r['profit_factor']:.2f}" if r['profit_factor']!=float('inf') else "∞"
            m = " ⭐" if r is best else ""
            print(f"   {r['label']:<42} {r['winrate']:>5.1f}% {r['wins']:>3}/{r['losses']:<5} {r['total_pips']:>+9.0f} {pf_s:>5} {r['trades']:>6}{m}")

        all_results.extend(results)

    # Overall best
    print("\n" + "═" * 75)
    print("🏆 OVERALL BEST BY PIPS:")
    top = sorted(all_results, key=lambda r: r['total_pips'], reverse=True)[:3]
    for r in top:
        print(f"   {r['label']:<42} WR={r['winrate']:>5.1f}% PF={r['profit_factor']} Pips={r['total_pips']:>+9.0f} Trades={r['trades']}")


if __name__ == "__main__":
    main()
