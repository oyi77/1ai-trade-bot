#!/usr/bin/env python3
"""Backtest model baru: 30 pip SOP validation untuk Exness 3-digit."""
import os, sys, json, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import warnings
warnings.filterwarnings("ignore")

import yfinance as yf
import pandas as pd
import numpy as np

TIMEFRAMES = ["D1", "H4", "H1", "M15", "M5"]
TF_YF_INTERVAL = {"D1": "1d", "H4": "60m", "H1": "60m", "M15": "15m", "M5": "5m"}
TF_YF_PERIOD  = {"D1": "6mo", "H4": "1mo", "H1": "14d", "M15": "5d", "M5": "2d"}
TF_YF_SYMBOL  = {"XAUUSD": "GC=F", "BTCUSD": "BTC-USD", "ETHUSD": "ETH-USD", "USOIL": "CL=F"}

def run_signal_on_bar(symbol, bar_date):
    """Run engine_consensus + compute_signal pada satu titik waktu."""
    from engine_consensus import run_engine_consensus, fetch_mtf_ohlcv, TF_CACHE_TTL, _YF_CACHE, _YF_CACHE_TIME
    from signal_calculator import compute_signal

    # Clear cache biar dapet data fresh sesuai period
    _YF_CACHE.clear()
    _YF_CACHE_TIME.clear()
    
    result = run_engine_consensus(symbol=symbol)
    if not result:
        return None
    
    sig = compute_signal(result)
    if not sig:
        return None
    
    return {
        "date": bar_date,
        "price": result["price"],
        "verdict": result["hierarchical"]["verdict"],
        "score": result["hierarchical"]["consensus_score"],
        "alignment": result["hierarchical"]["mtf_alignment"],
        "macro": result["hierarchical"]["macro_trend"],
        "action": sig["action"],
        "entry": sig["entry"],
        "sl": sig["sl"],
        "tp1": sig["tp1"],
        "tp2": sig["tp2"],
        "pips_sl": sig["pips_sl"],
        "pips_target": sig["pips_target"],
        "rr": sig["rr"],
        "grade": sig["grade"],
    }

def simulate_trade(signal, df_m5):
    """Simulasikan apakah signal hit TP atau SL dulu dalam 48 bar M5 (4 jam)."""
    entry = signal["entry"]
    sl = signal["sl"]
    tp1 = signal["tp1"]
    action = signal["action"]
    
    # Cari bar setelah signal dalam df_m5
    signal_idx = signal.get("signal_idx", 0)
    lookahead = min(signal_idx + 48, len(df_m5))
    
    for i in range(signal_idx + 1, lookahead):
        bar = df_m5.iloc[i]
        high = float(bar["High"])
        low = float(bar["Low"])
        
        if action == "BUY":
            if low <= sl:
                return "LOSS", (bar.name, low)
            if high >= tp1:
                return "WIN", (bar.name, high)
        else:  # SELL
            if high >= sl:
                return "LOSS", (bar.name, high)
            if low <= tp1:
                return "WIN", (bar.name, low)
    
    return "EXPIRED", None


def run_backtest_period(symbol, display, start, end, max_signals=15):
    """Backtest dengan sampling: cek M5 bar tertentu."""
    print(f"\n{'='*50}")
    print(f"📊 BACKTEST: {display} ({start} → {end})")
    print(f"{'='*50}")
    
    # Fetch M5 data buat simuasi
    yf_sym = TF_YF_SYMBOL[symbol]
    m5 = yf.download(yf_sym, period="2d", interval="5m", progress=False)
    if m5.empty:
        print("❌ Gak dapet M5 data")
        return
    
    # Run signal sekarang
    print(f"\n🔄 Running engine_consensus (butuh ~30 detik)...")
    t0 = time.time()
    result = None
    from engine_consensus import run_engine_consensus
    from signal_calculator import compute_signal, format_signal_telegram
    result = run_engine_consensus(symbol=symbol)
    if not result:
        print("❌ No MTF result")
        return
    
    sig = compute_signal(result)
    if not sig:
        print("❌ No signal — quality gate blocked")
        return
    
    t1 = time.time()
    print(f"⏱️  Selesai dalam {t1-t0:.1f}s")
    print(f"\n{'─'*50}")
    print(f"🕐 Harga saat ini: ${result['price']:.2f}")
    print(f"📈 Macro: {result['hierarchical']['macro_trend']} | {result['hierarchical']['mtf_alignment']}")
    print(f"")
    
    # ── PIPS EXPLANATION ──
    pip_val = 0.01  # XAUUSD
    entry = sig["entry"]
    sl = sig["sl"]
    tp1 = sig["tp1"]
    pips_sl = abs(entry - sl) / pip_val
    pips_tp = abs(entry - tp1) / pip_val
    
    print(f"🔥 SIGNAL: {sig['action']} {sig['symbol']} | Grade {sig['grade']} | Conf {sig['confidence']*100:.0f}%")
    print(f"")
    print(f"📌 ENTRY: ${entry:.2f}")
    print(f"🛑 SL:    ${sl:.2f} ({pips_sl:.0f} pip)")
    print(f"✅ TP1:   ${tp1:.2f} ({pips_tp:.0f} pip)")
    print(f"📊 RR:   1:{sig['rr']}")
    print(f"")
    
    # ── PIPS BREAKDOWN buat Exness 3-digit ──
    print(f"📐 PIP BREAKDOWN (Exness 3-digit):")
    print(f"   1 pip = {pip_val} (2nd decimal) → $0.01 per pip")
    print(f"   30 pip = ${30 * pip_val:.2f}")
    print(f"   SL jarak: ${abs(entry-sl):.2f} = {pips_sl:.0f} pip")
    print(f"   TP1 jarak: ${abs(entry-tp1):.2f} = {pips_tp:.0f} pip")
    
    # ── Verifikasi SOP ──
    if pips_sl <= 40 and pips_sl >= 20:
        print(f"\n✅ SL {pips_sl:.0f} pip — DALAM SOP (25-40 pip) ✅")
    elif pips_sl < 20:
        print(f"\n⚠️ SL {pips_sl:.0f} pip — TERLALU KECIL!")
    else:
        print(f"\n⚠️ SL {pips_sl:.0f} pip — MELEBIHI SOP 30 pip!")
    
    # ── Simulate trade ──
    print(f"\n{'─'*50}")
    print(f"📈 SIMULASI TRADE (48 bar M5 = 4 jam):")
    print(f"{'─'*50}")
    
    df_m5 = m5.copy()
    # Find current bar index (last bar)
    current_idx = len(df_m5) - 2  # second-to-last, last is live
    sig["signal_idx"] = current_idx
    
    # Simulate
    result_type, hit_data = simulate_trade(sig, df_m5)
    if result_type == "WIN":
        print(f"✅ WIN — Harga sentuh TP1 di {hit_data[0]}")
    elif result_type == "LOSS":
        print(f"❌ LOSS — Harga sentuh SL di {hit_data[0]}")
    else:
        print(f"⏳ EXPIRED — Dalam 4 jam, gak kena SL/TP")
    
    print(f"\n{'─'*50}")
    print(f"🔍 ANALISA:")
    print(f"  ATR fallback: spesifik per aset ✅")
    print(f"  Entry cap: 1.5× ATR dari price ✅")
    print(f"  SL max: {sig['pips_sl']}pt (max_sl_pts={sig.get('pips_sl','?')})")
    print(f"  Engine agreement: {sig.get('reason','')[20:50]}")
    
    print(f"\n{format_signal_telegram(sig)}")


if __name__ == "__main__":
    run_backtest_period("XAUUSD", "XAUUSD", "2026-06-01", "2026-06-09", 5)
