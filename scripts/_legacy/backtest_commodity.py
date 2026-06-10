#!/usr/bin/env python3
"""Commodity backtest — USOIL, Silver, Natural Gas."""
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

START, END = "2025-12-08", "2026-06-08"
INTERVAL, MIN_ENGINES = "1h", 2
MIN_RR, MAX_RR = 1.0, 5.0

COMMODITIES = [
    ("CL=F", "USOIL", [
        ("OIL A: SL0.50 TP1.00 (RR 1:2)",    0.50, 1.00),
        ("OIL B: SL0.30 TP0.60 (RR 1:2)",    0.30, 0.60),
        ("OIL C: SL0.70 TP1.40 (RR 1:2)",    0.70, 1.40),
        ("OIL D: SL0.50 TP0.75 (RR 1:1.5)",  0.50, 0.75),
        ("OIL E: SL0.40 TP0.60 (RR 1:1.5)",  0.40, 0.60),
    ], 0.10),  # min_sl_dist
    ("SI=F", "SILVER", [
        ("SIL A: SL0.30 TP0.60 (RR 1:2)",    0.30, 0.60),
        ("SIL B: SL0.20 TP0.40 (RR 1:2)",    0.20, 0.40),
        ("SIL C: SL0.40 TP0.80 (RR 1:2)",    0.40, 0.80),
        ("SIL D: SL0.30 TP0.45 (RR 1:1.5)",  0.30, 0.45),
        ("SIL E: SL0.20 TP0.30 (RR 1:1.5)",  0.20, 0.30),
    ], 0.05),
    ("NG=F", "NATGAS", [
        ("GAS A: SL0.15 TP0.30 (RR 1:2)",    0.15, 0.30),
        ("GAS B: SL0.10 TP0.20 (RR 1:2)",    0.10, 0.20),
        ("GAS C: SL0.20 TP0.40 (RR 1:2)",    0.20, 0.40),
        ("GAS D: SL0.15 TP0.22 (RR 1:1.5)",  0.15, 0.225),
        ("GAS E: SL0.10 TP0.15 (RR 1:1.5)",  0.10, 0.15),
    ], 0.03),
]


def run_one_config(df, sl_pts, tp_pts, min_sl, label):
    from engine_consensus import run_engine_consensus
    trades, in_trade = [], None

    for i in range(50, len(df)):
        window = df.iloc[i-50:i+1].copy()
        cp = float(window["Close"].iloc[-1]); ch = float(window["High"].iloc[-1])
        cl = float(window["Low"].iloc[-1]); bt = window.index[-1]

        if in_trade:
            t = in_trade
            if t["action"] == "BUY":
                if cl <= t["sl"]: t["outcome"]="SL_HIT"; t["close_price"]=t["sl"]; t["close_time"]=str(bt); trades.append(t); in_trade=None
                elif ch >= t["tp"]: t["outcome"]="TP_HIT"; t["close_price"]=t["tp"]; t["close_time"]=str(bt); trades.append(t); in_trade=None
            else:
                if ch >= t["sl"]: t["outcome"]="SL_HIT"; t["close_price"]=t["sl"]; t["close_time"]=str(bt); trades.append(t); in_trade=None
                elif cl <= t["tp"]: t["outcome"]="TP_HIT"; t["close_price"]=t["tp"]; t["close_time"]=str(bt); trades.append(t); in_trade=None
            continue

        ohlcv_list = [{"timestamp": idx.timestamp() if hasattr(idx,'timestamp') else 0,
                        "open": float(r["Open"]), "high": float(r["High"]),
                        "low": float(r["Low"]), "close": float(r["Close"])}
                       for idx, r in window.iterrows()]
        try:
            er = run_engine_consensus(ohlcv_list, cp, "USOIL")
        except: continue
        if not er: continue
        bc, sc = er.get("buy_count",0), er.get("sell_count",0)
        if bc+sc < MIN_ENGINES: continue
        action = "BUY" if bc > sc else "SELL"

        entry=cp
        sl=round(entry-sl_pts,2) if action=="BUY" else round(entry+sl_pts,2)
        tp=round(entry+tp_pts,2) if action=="BUY" else round(entry-tp_pts,2)
        sd=abs(entry-sl); td=abs(tp-entry)
        if sd<min_sl: continue
        rr=round(td/sd,2) if sd>0 else 0
        if rr<MIN_RR or rr>MAX_RR: continue

        in_trade={"action":action,"entry":entry,"sl":sl,"tp":tp,"rr":rr,"engines":bc+sc,
                  "open_time":str(bt),"outcome":None,"close_price":None,"close_time":None}

    if in_trade:
        in_trade["outcome"]="OPEN"; in_trade["close_price"]=float(df["Close"].iloc[-1])
        in_trade["close_time"]=str(df.index[-1]); trades.append(in_trade)

    wins=[t for t in trades if t["outcome"]=="TP_HIT"]
    losses=[t for t in trades if t["outcome"]=="SL_HIT"]
    total=len(trades)
    wr=(len(wins)/total*100) if total>0 else 0
    tpips=sum((t["tp"]-t["entry"]) if t["action"]=="BUY" else (t["entry"]-t["tp"]) for t in wins) \
         - sum((t["entry"]-t["sl"]) if t["action"]=="BUY" else (t["sl"]-t["entry"]) for t in losses)
    aw=round(sum(abs(t["tp"]-t["entry"]) for t in wins)/len(wins),2) if wins else 0
    al=round(sum(abs(t["sl"]-t["entry"]) for t in losses)/len(losses),2) if losses else 0
    pf=round(aw/al,2) if al>0 else float('inf')
    return {"label":label,"trades":total,"wins":len(wins),"losses":len(losses),
            "winrate":round(wr,1),"total_pips":round(tpips,2),
            "avg_win":aw,"avg_loss":al,"profit_factor":pf}


def main():
    print(f"📊 COMMODITY BACKTEST — 6 Bulan\n")

    for sym, disp, configs, min_sl in COMMODITIES:
        print(f"🔹 {disp} ({sym})")
        t = yf.Ticker(sym)
        df = t.history(start=START, end=END, interval=INTERVAL)
        if df.empty: print("   ❌ No data\n"); continue
        df = df.reset_index()
        print(f"   {len(df)} bars | ${df['Close'].min():.2f} – ${df['Close'].max():.2f}")

        results = []
        for label, sl, tp in configs:
            print(f"   🧪 {label:<42}", end=" ", flush=True)
            r = run_one_config(df, sl, tp, min_sl, f"{disp} {label}")
            results.append(r)
            print(f"WR={r['winrate']:>5.1f}% PF={r['profit_factor']:>5} Pips={r['total_pips']:>+8.2f} T={r['trades']:>4}")

        print(f"\n   {'Config':<42} {'WR%':>6} {'W/L':>8} {'Pips':>9} {'PF':>6} {'Trades':>6}")
        print("   " + "─"*82)
        best = max(results, key=lambda r: r['total_pips'])
        for r in results:
            pf_s = f"{r['profit_factor']:.2f}" if r['profit_factor']!=float('inf') else "∞"
            m = " ⭐" if r is best else ""
            print(f"   {r['label']:<42} {r['winrate']:>5.1f}% {r['wins']:>3}/{r['losses']:<5} {r['total_pips']:>+8.2f} {pf_s:>5} {r['trades']:>6}{m}")
        print()


if __name__ == "__main__":
    main()
