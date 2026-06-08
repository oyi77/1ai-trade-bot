#!/usr/bin/env python3
"""Winrate booster tests — daily trend filter, consecutive bar confirm, ATR filter."""
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

START = "2025-12-08"
END = "2026-06-08"
MIN_ENGINES = 2
MIN_RR, MAX_RR = 1.0, 5.0

ASSETS = [
    ("GC=F", "XAUUSD", 32, 52, 2.0),
    ("BTC-USD", "BTCUSD", 600, 1200, 30.0),
    ("ETH-USD", "ETHUSD", 50, 75, 5.0),
]

FILTERS = [
    ("NO FILTER (baseline)", None),
    ("Daily Trend (price vs EMA20 daily)", "daily_trend"),
    ("Consecutive 2-bar confirm", "consec_2bar"),
    ("Daily Trend + 2-bar confirm", "daily_trend+consec"),
]


def daily_trend_ok(df_1h, bar_idx, action):
    """Check if 1h bar aligns with daily trend (price vs daily SMA20)."""
    # Use recent daily data approximation: SMA20 of last 20 daily closes
    # Since we only have 1h data, use last ~480 bars (20 days * 24h)
    lookback = min(480, bar_idx)
    if lookback < 24:  # need at least 1 day
        return True
    daily_sma = float(df_1h["Close"].iloc[max(0,bar_idx-lookback):bar_idx+1].tail(480).mean())
    current = float(df_1h["Close"].iloc[bar_idx])
    if action == "BUY" and current > daily_sma:
        return True
    if action == "SELL" and current < daily_sma:
        return True
    return False


def run_one_config(df, sl_pts, tp_pts, min_sl, filter_mode, label):
    from engine_consensus import run_engine_consensus
    trades, in_trade = [], None
    last_signal_action = None  # for consec_2bar

    for i in range(50, len(df)):
        window = df.iloc[i-50:i+1].copy()
        cp = float(window["Close"].iloc[-1])
        ch = float(window["High"].iloc[-1])
        cl = float(window["Low"].iloc[-1])
        bt = window.index[-1]

        if in_trade:
            t = in_trade
            if t["action"] == "BUY":
                if cl <= t["sl"]:
                    t["outcome"]="SL_HIT"; t["close_price"]=t["sl"]; t["close_time"]=str(bt)
                    trades.append(t); in_trade=None
                elif ch >= t["tp"]:
                    t["outcome"]="TP_HIT"; t["close_price"]=t["tp"]; t["close_time"]=str(bt)
                    trades.append(t); in_trade=None
            else:
                if ch >= t["sl"]:
                    t["outcome"]="SL_HIT"; t["close_price"]=t["sl"]; t["close_time"]=str(bt)
                    trades.append(t); in_trade=None
                elif cl <= t["tp"]:
                    t["outcome"]="TP_HIT"; t["close_price"]=t["tp"]; t["close_time"]=str(bt)
                    trades.append(t); in_trade=None
            continue

        ohlcv_list = [{"timestamp": idx.timestamp() if hasattr(idx,'timestamp') else 0,
                        "open": float(r["Open"]), "high": float(r["High"]),
                        "low": float(r["Low"]), "close": float(r["Close"])}
                       for idx, r in window.iterrows()]

        try:
            er = run_engine_consensus(ohlcv_list, cp, "XAUUSD")
        except Exception:
            continue
        if not er: continue

        bc = er.get("buy_count",0); sc = er.get("sell_count",0)
        if bc+sc < MIN_ENGINES: continue
        action = "BUY" if bc > sc else "SELL"

        # ── Filters ──
        if filter_mode and "daily_trend" in filter_mode:
            if not daily_trend_ok(df, i, action):
                continue

        if filter_mode and "consec" in filter_mode:
            if action != last_signal_action:
                last_signal_action = action
                continue  # skip this bar, wait for confirmation
            # else: confirmed (same signal 2 bars in a row)

        last_signal_action = action

        if action == "BUY":
            entry=cp; sl=round(entry-sl_pts,2); tp=round(entry+tp_pts,2)
        else:
            entry=cp; sl=round(entry+sl_pts,2); tp=round(entry-tp_pts,2)

        sd=abs(entry-sl); td=abs(tp-entry); rr=round(td/sd,2) if sd>0 else 0
        if sd<min_sl or rr<MIN_RR or rr>MAX_RR: continue

        in_trade={"action":action,"entry":entry,"sl":sl,"tp":tp,"rr":rr,
                  "engines":bc+sc,"open_time":str(bt),"outcome":None,
                  "close_price":None,"close_time":None}

    if in_trade:
        in_trade["outcome"]="OPEN"
        in_trade["close_price"]=float(df["Close"].iloc[-1])
        in_trade["close_time"]=str(df.index[-1])
        trades.append(in_trade)

    wins=[t for t in trades if t["outcome"]=="TP_HIT"]
    losses=[t for t in trades if t["outcome"]=="SL_HIT"]
    total=len(trades)
    wr=(len(wins)/total*100) if total>0 else 0
    tpips=sum((t["tp"]-t["entry"]) if t["action"]=="BUY" else (t["entry"]-t["tp"]) for t in wins) \
         - sum((t["entry"]-t["sl"]) if t["action"]=="BUY" else (t["sl"]-t["entry"]) for t in losses)
    aw=round(sum(abs(t["tp"]-t["entry"]) for t in wins)/len(wins),1) if wins else 0
    al=round(sum(abs(t["sl"]-t["entry"]) for t in losses)/len(losses),1) if losses else 0
    pf=round(aw/al,2) if al>0 else float('inf')
    return {"label":label,"trades":total,"wins":len(wins),"losses":len(losses),
            "winrate":round(wr,1),"total_pips":round(tpips,1),
            "avg_win":aw,"avg_loss":al,"profit_factor":pf}


def main():
    print("📊 WINRATE BOOSTER BACKTEST — 6 Bulan\n")
    for sym, disp, sl, tp, minsl in ASSETS:
        print(f"🔹 {disp} (SL={sl} TP={tp})")
        t = yf.Ticker(sym)
        df = t.history(start=START, end=END, interval="1h")
        if df.empty: print("   ❌ No data"); continue
        df = df.reset_index()
        print(f"   {len(df)} bars | ${df['Close'].min():.0f} – ${df['Close'].max():.0f}\n")

        results = []
        for fname, fmode in FILTERS:
            print(f"   🧪 {fname:<40}", end=" ", flush=True)
            r = run_one_config(df, sl, tp, minsl, fmode, f"{disp} {fname}")
            results.append(r)
            print(f"WR={r['winrate']:>5.1f}% PF={r['profit_factor']:>5} Pips={r['total_pips']:>+8.0f} T={r['trades']:>4}")

        print(f"\n   {'Filter':<40} {'WR%':>6} {'W/L':>8} {'Pips':>9} {'PF':>6} {'Trades':>6} {'ΔWR':>6}")
        print("   " + "─"*85)
        base_wr = results[0]["winrate"] if results else 0
        for r in results:
            pf_s = f"{r['profit_factor']:.2f}" if r['profit_factor']!=float('inf') else "∞"
            dwr = f"{r['winrate']-base_wr:+.1f}%" if base_wr>0 else "—"
            print(f"   {r['label']:<40} {r['winrate']:>5.1f}% {r['wins']:>3}/{r['losses']:<5} {r['total_pips']:>+8.0f} {pf_s:>5} {r['trades']:>6} {dwr:>6}")
        print()


if __name__ == "__main__":
    main()
