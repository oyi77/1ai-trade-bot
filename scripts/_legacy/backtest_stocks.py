#!/usr/bin/env python3
"""Stock backtest — Indonesian stocks with percentage-based SL/TP."""
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
INTERVAL = "1h"
MIN_ENGINES = 2
MIN_RR, MAX_RR = 1.0, 5.0

STOCKS = [
    # (symbol, display, configs: [(label, sl_pct, tp_pct)])
    ("BBCA.JK", "BBCA", [
        ("SL1% TP2% (RR 1:2)",      0.010, 0.020),
        ("SL0.5% TP1% (RR 1:2)",    0.005, 0.010),
        ("SL1.5% TP2.25% (RR 1:1.5)", 0.015, 0.0225),
        ("SL1% TP1.5% (RR 1:1.5)",  0.010, 0.015),
        ("SL2% TP3% (RR 1:1.5)",    0.020, 0.030),
    ]),
    ("BBRI.JK", "BBRI", [
        ("SL1% TP2% (RR 1:2)",      0.010, 0.020),
        ("SL0.5% TP1% (RR 1:2)",    0.005, 0.010),
        ("SL1.5% TP2.25% (RR 1:1.5)", 0.015, 0.0225),
        ("SL1% TP1.5% (RR 1:1.5)",  0.010, 0.015),
    ]),
    ("TLKM.JK", "TLKM", [
        ("SL1% TP2% (RR 1:2)",      0.010, 0.020),
        ("SL0.5% TP1% (RR 1:2)",    0.005, 0.010),
        ("SL1% TP1.5% (RR 1:1.5)",  0.010, 0.015),
    ]),
    ("ASII.JK", "ASII", [
        ("SL1% TP2% (RR 1:2)",      0.010, 0.020),
        ("SL1% TP1.5% (RR 1:1.5)",  0.010, 0.015),
    ]),
]


def run_one_config(df, sl_pct, tp_pct, min_sl_price, label):
    from engine_consensus import run_engine_consensus
    trades, in_trade = [], None

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
            er = run_engine_consensus(ohlcv_list, cp, "BBCA")
        except Exception:
            continue
        if not er: continue

        bc = er.get("buy_count",0); sc = er.get("sell_count",0)
        if bc+sc < MIN_ENGINES: continue
        action = "BUY" if bc > sc else "SELL"

        if action == "BUY":
            entry=cp; sl=round(entry*(1-sl_pct),0); tp=round(entry*(1+tp_pct),0)
        else:
            entry=cp; sl=round(entry*(1+sl_pct),0); tp=round(entry*(1-tp_pct),0)

        sd=abs(entry-sl); td=abs(tp-entry)
        if sd < min_sl_price: continue
        rr=round(td/sd,2) if sd>0 else 0
        if rr<MIN_RR or rr>MAX_RR: continue

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
    aw=round(sum(abs(t["tp"]-t["entry"]) for t in wins)/len(wins),0) if wins else 0
    al=round(sum(abs(t["sl"]-t["entry"]) for t in losses)/len(losses),0) if losses else 0
    pf=round(aw/al,2) if al>0 else float('inf')
    return {"label":label,"trades":total,"wins":len(wins),"losses":len(losses),
            "winrate":round(wr,1),"total_pips":round(tpips,0),
            "avg_win":aw,"avg_loss":al,"profit_factor":pf}


def main():
    print(f"📊 SAHAM INDO BACKTEST — 6 Bulan ({START} → {END})\n")

    for sym, disp, configs in STOCKS:
        print(f"🔹 {disp} ({sym})")
        t = yf.Ticker(sym)
        df = t.history(start=START, end=END, interval=INTERVAL)
        if df.empty:
            print("   ❌ No data\n"); continue
        df = df.reset_index()
        avg_p = float(df["Close"].mean())
        min_sl = round(avg_p * 0.002, 0)  # 0.2% minimum SL
        print(f"   {len(df)} bars | Rp {df['Close'].min():.0f} – Rp {df['Close'].max():.0f} | Avg Rp {avg_p:.0f}")

        results = []
        for label, slp, tpp in configs:
            print(f"   🧪 {label:<35}", end=" ", flush=True)
            r = run_one_config(df, slp, tpp, min_sl, f"{disp} {label}")
            results.append(r)
            print(f"WR={r['winrate']:>5.1f}% PF={r['profit_factor']:>5} Pips=Rp{r['total_pips']:>+8.0f} T={r['trades']:>4}")

        print(f"\n   {'Config':<35} {'WR%':>6} {'W/L':>8} {'Pips(Rp)':>12} {'PF':>6} {'Trades':>6}")
        print("   " + "─"*80)
        for r in results:
            pf_s = f"{r['profit_factor']:.2f}" if r['profit_factor']!=float('inf') else "∞"
            best = " ⭐" if r["total_pips"] == max(x["total_pips"] for x in results) else ""
            print(f"   {r['label']:<35} {r['winrate']:>5.1f}% {r['wins']:>3}/{r['losses']:<5} Rp{r['total_pips']:>+10.0f} {pf_s:>5} {r['trades']:>6}{best}")
        print()


if __name__ == "__main__":
    main()
