#!/usr/bin/env python3
"""Crypto backtest — cached, optimized. Only runs BTC A (SL500 TP1000)."""
import os, sys, json
sys.path.insert(0, '/home/openclaw/projects/1ai-trade-bot/scripts')
import yfinance as yf
import numpy as np

# Pre-cache all timeframes first
print("📦 Pre-caching yfinance data for BTC-USD...")
ticker = yf.Ticker("BTC-USD")
for tf,interval,period in [('D1','1d','6mo'),('H4','60m','1mo'),('H1','60m','1mo'),('M15','15m','5d'),('M5','5m','2d')]:
    df = ticker.history(period=period, interval=interval)
    print(f"  {tf}: {len(df)} bars cached")

print("📦 Pre-caching for ETH-USD...")
ticker_eth = yf.Ticker("ETH-USD")
for tf,interval,period in [('D1','1d','6mo'),('H4','60m','1mo'),('H1','60m','1mo'),('M15','15m','5d'),('M5','5m','2d')]:
    df = ticker_eth.history(period=period, interval=interval)
    print(f"  {tf}: {len(df)} bars cached")

# Now do engine backtest
from engine_consensus import run_engine_consensus

def run_config(symbol, df, sl_pts, tp_pts, label):
    MIN_ENGINES = 2
    trades = []
    in_trade = None
    total_bars = len(df)
    last_report = 0

    for i in range(50, total_bars, 4):  # stride=4 → every 4h, saves 75% time
        if i - last_report >= 200:
            print(f"  Processing bar {i}/{total_bars}...")
            last_report = i

        current_price = float(df['Close'].iloc[i])
        current_high = float(df['High'].iloc[i])
        current_low = float(df['Low'].iloc[i])

        if in_trade:
            t = in_trade
            if t['action'] == 'BUY':
                if current_low <= t['sl']:
                    t['outcome'] = 'SL_HIT'; t['close_price'] = t['sl']
                    trades.append(t); in_trade = None
                elif current_high >= t['tp']:
                    t['outcome'] = 'TP_HIT'; t['close_price'] = t['tp']
                    trades.append(t); in_trade = None
            else:
                if current_high >= t['sl']:
                    t['outcome'] = 'SL_HIT'; t['close_price'] = t['sl']
                    trades.append(t); in_trade = None
                elif current_low <= t['tp']:
                    t['outcome'] = 'TP_HIT'; t['close_price'] = t['tp']
                    trades.append(t); in_trade = None
            continue

        try:
            result = run_engine_consensus(symbol=symbol, price=current_price)
        except Exception:
            continue
        if not result:
            continue

        buy_count = result.get('buy_count', 0)
        sell_count = result.get('sell_count', 0)
        active = buy_count + sell_count
        if active < MIN_ENGINES:
            continue

        action = 'BUY' if buy_count > sell_count else 'SELL'
        if action == 'BUY':
            entry = current_price
            sl = round(entry - sl_pts, 2)
            tp = round(entry + tp_pts, 2)
        else:
            entry = current_price
            sl = round(entry + sl_pts, 2)
            tp = round(entry - tp_pts, 2)

        in_trade = {
            'action': action, 'entry': entry, 'sl': sl, 'tp': tp,
            'open_time': str(df.index[i]), 'outcome': None,
            'close_price': None, 'close_time': None,
        }

    wins = [t for t in trades if t['outcome'] == 'TP_HIT']
    losses = [t for t in trades if t['outcome'] == 'SL_HIT']
    total = len(trades)
    wr = (len(wins)/total*100) if total > 0 else 0
    total_pips = sum(t['tp']-t['entry'] for t in wins if t['action']=='BUY') + sum(t['entry']-t['tp'] for t in wins if t['action']=='SELL') - sum(t['entry']-t['sl'] for t in losses if t['action']=='BUY') - sum(t['sl']-t['entry'] for t in losses if t['action']=='SELL')
    aw = round(sum(abs(t['tp']-t['entry']) for t in wins)/len(wins),1) if wins else 0
    al = round(sum(abs(t['sl']-t['entry']) for t in losses)/len(losses),1) if losses else 0

    return {
        'label': label, 'trades': total, 'wins': len(wins), 'losses': len(losses),
        'winrate': round(wr,1), 'total_pips': round(total_pips,1),
        'avg_win': aw, 'avg_loss': al,
    }

print(f"\n{'='*60}")
print("🚀 BTC BACKTEST — 1 config (BTC A: SL500 TP1000)")
print(f"{'='*60}")
df = ticker.history(start='2025-12-08', end='2026-06-08', interval='1h')
print(f"Bars: {len(df)}")
r = run_config('BTCUSD', df, 500, 1000, 'BTC A: SL500 TP1000')
print(f"\n{'─'*40}")
print(f"  WR: {r['winrate']}%  | Trades: {r['trades']} ({r['wins']}W/{r['losses']}L)")
print(f"  Pips: {r['total_pips']:+}  | Avg Win: {r['avg_win']}  | Avg Loss: {r['avg_loss']}")
print(f"{'─'*40}")

# Save result
out = '/home/openclaw/projects/1ai-trade-bot/data/vilona_tradefx/backtest_crypto_result.json'
with open(out, 'w') as f:
    json.dump(r, f, indent=2)
print(f"✅ Saved to {out}")
