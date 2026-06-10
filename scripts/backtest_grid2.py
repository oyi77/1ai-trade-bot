#!/usr/bin/env python3
"""Extended grid backtest — more SL/TP combos + EMA trend filter + killzone filter."""
import os, sys, json
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
MIN_SL_DIST = 2.0
MIN_RR = 1.0
MAX_RR = 5.0

# ── Filters ──
def add_ema(df, period=20):
    """Add EMA column."""
    ema = df["Close"].ewm(span=period, adjust=False).mean()
    return ema

def is_london_session(dt):
    """London: 08:00-17:00 UTC (winter)."""
    return 8 <= dt.hour < 17

def is_ny_session(dt):
    """NY: 13:00-22:00 UTC (winter)."""
    return 13 <= dt.hour < 22

def is_killzone(dt):
    """London or NY session."""
    return is_london_session(dt) or is_ny_session(dt)

# ── Config Grid ──
CONFIGS = [
    # Baseline (current)
    ("A: 2eng SL30 TP45 (current)",       2, 30, 45, False, False),

    # New SL/TP combos
    ("I: 2eng SL30 TP50 (RR 1:1.67)",     2, 30, 50, False, False),
    ("J: 2eng SL30 TP55 (RR 1:1.83)",     2, 30, 55, False, False),
    ("K: 2eng SL28 TP50 (RR 1:1.79)",     2, 28, 50, False, False),
    ("L: 2eng SL32 TP52 (RR 1:1.625)",    2, 32, 52, False, False),

    # With EMA200 trend filter
    ("M: 2eng SL30 TP45 + EMA200",        2, 30, 45, True,  False),
    ("N: 2eng SL30 TP50 + EMA200",        2, 30, 50, True,  False),
    ("O: 2eng SL28 TP50 + EMA200",        2, 28, 50, True,  False),

    # With Killzone filter
    ("P: 2eng SL30 TP45 + Killzone",      2, 30, 45, False, True),
    ("Q: 2eng SL30 TP50 + Killzone",      2, 30, 50, False, True),
    ("R: 2eng SL28 TP50 + Killzone",      2, 28, 50, False, True),

    # EMA200 + Killzone (combo)
    ("S: 2eng SL30 TP45 + EMA+KZ",        2, 30, 45, True,  True),
    ("T: 2eng SL30 TP50 + EMA+KZ",        2, 30, 50, True,  True),
    ("U: 2eng SL28 TP50 + EMA+KZ",        2, 28, 50, True,  True),
]


def run_one_config(df, min_eng, sl_pts, tp_pts, ema_filter, kz_filter, label):
    from engine_consensus import run_engine_consensus

    # Pre-compute EMA
    ema20 = None
    if ema_filter:
        ema20 = add_ema(df, 20)

    trades = []
    in_trade = None
    total_signals = 0

    for i in range(50, len(df)):
        window = df.iloc[i-50:i+1].copy()
        current_price = float(window["Close"].iloc[-1])
        current_high = float(window["High"].iloc[-1])
        current_low = float(window["Low"].iloc[-1])
        bar_time = window.index[-1]

        # Killzone filter
        if kz_filter:
            ts = pd.Timestamp(bar_time)
            if not is_killzone(ts):
                # Still check open trade
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

        # Check open trade
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
            if in_trade is None:
                continue
            if not kz_filter:
                continue
            # If killzone filter active, don't open new trades outside killzone
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

        buy_count = engine_result.get("buy_count", 0)
        sell_count = engine_result.get("sell_count", 0)
        active_count = buy_count + sell_count

        if active_count < min_eng:
            continue

        if buy_count > sell_count:
            action = "BUY"
        else:
            action = "SELL"

        # EMA trend filter
        if ema_filter and ema20 is not None:
            ema_val = float(ema20.iloc[i])
            if not np.isnan(ema_val):
                if action == "BUY" and current_price < ema_val:
                    continue  # BUY below EMA = counter-trend, skip
                if action == "SELL" and current_price > ema_val:
                    continue  # SELL above EMA = counter-trend, skip

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
        "min_engines": min_eng, "sl_pts": sl_pts, "tp_pts": tp_pts,
        "ema_filter": ema_filter, "kz_filter": kz_filter,
        "signals": total_signals, "trades": total,
        "wins": len(wins), "losses": len(losses),
        "winrate": round(wr, 1),
        "total_pips": round(total_pips, 1),
        "avg_win": avg_win, "avg_loss": avg_loss,
        "profit_factor": pf,
    }


def main():
    print(f"📊 EXTENDED GRID BACKTEST — {DISPLAY}")
    print(f"   Data: {START_DATE} → {END_DATE} ({INTERVAL})")
    print(f"   Configs: {len(CONFIGS)} (SL/TP combos + EMA + Killzone)")
    print("━" * 80)

    ticker = yf.Ticker(SYMBOL)
    df = ticker.history(start=START_DATE, end=END_DATE, interval=INTERVAL)
    if df.empty:
        print("❌ No data")
        return
    df = df.reset_index()
    print(f"✅ {len(df)} bars loaded\n")

    results = []
    for i, (label, me, sl, tp, ema, kz) in enumerate(CONFIGS):
        print(f"🧪 [{i+1:>2}/{len(CONFIGS)}] {label:<45}", end=" ", flush=True)
        r = run_one_config(df, me, sl, tp, ema, kz, label)
        results.append(r)
        print(f"WR={r['winrate']:>5.1f}% PF={r['profit_factor']:>5} T={r['trades']:>4} Pips={r['total_pips']:>+6.0f}")

    # ── Table ──
    print("\n" + "━" * 80)
    print(f"{'Config':<45} {'WR%':>6} {'W/L':>8} {'Pips':>8} {'PF':>6} {'Trades':>6}")
    print("━" * 80)

    best_pf = max(results, key=lambda r: (r['profit_factor'], r['total_pips']) if r['profit_factor'] != float('inf') else (0,0))
    best_wr = max(results, key=lambda r: r['winrate'])
    best_pips = max(results, key=lambda r: r['total_pips'])

    for r in results:
        pf_str = f"{r['profit_factor']:.2f}" if r['profit_factor'] != float('inf') else "∞"
        marker = " " + " ".join([
            "💰" if r is best_pf else "",
            "🎯" if r is best_wr else "",
            "📈" if r is best_pips else "",
        ]).strip()
        print(f"{r['label']:<45} {r['winrate']:>5.1f}% {r['wins']:>3}/{r['losses']:<5} {r['total_pips']:>+7.0f} {pf_str:>5} {r['trades']:>6}{marker}")

    print("━" * 80)
    print(f"💰 Best PF | 🎯 Best WR | 📈 Best Pips")
    print(f"\n📊 TOP 5 by Total Pips:")
    top5 = sorted(results, key=lambda r: r['total_pips'], reverse=True)[:5]
    for r in top5:
        print(f"   {r['label']:<45} WR={r['winrate']:>5.1f}% PF={r['profit_factor']} Pips={r['total_pips']:>+6.0f} Trades={r['trades']}")

    # Save
    out_path = Path(__file__).resolve().parent.parent / "data" / "vilona_tradefx" / "backtest_grid2.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2, default=str))
    print(f"\n📁 {out_path}")


if __name__ == "__main__":
    main()
