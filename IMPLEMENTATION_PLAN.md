# Implementation Plan — Signals Unification + Competitor Features

## 🚨 CRITICAL: Restore Lost Legacy Features

### Phase 0: Lost Commands (5 missing from unification)

| Command | Description | Status | Priority |
|---------|-------------|--------|----------|
| `/levels` | SnR + FIBO + Engine Deep Dive (donor-locked) | ❌ LOST | HIGH |
| `/news` | Grok News X/Twitter intel (donor-locked) | ❌ LOST | HIGH |
| `/zones` | Order Blocks + FVG + Supply/Demand | ❌ LOST | MEDIUM |
| `/structure` | BOS/CHoCH + Trend + MTF Alignment | ❌ LOST | MEDIUM |
| `/session` | Killzone + Session High/Low + Range | ❌ LOST | MEDIUM |

**Action:** Re-implement from `scripts/_legacy/vilona_tradefx_handler.py`

### Phase 0b: Stats & Reports (check if working)

| Feature | Legacy | Current | Status |
|---------|--------|---------|--------|
| `/winrate` | trade_tracker.get_stats() | `_cmd_winrate` | ✅ EXISTS |
| `/history` | trade_tracker.get_recent_trades() | `_cmd_history` | ✅ EXISTS |
| `/recap` | trade_tracker.get_daily_trades() | `_cmd_recap` | ✅ EXISTS |
| `/mapping` | yfinance pivot calc | `_cmd_mapping` | ✅ EXISTS |
| Trade result alert (TP) | format_trade_close_alert() | No direct broadcast | ❌ MISSING |
| Trade result alert (SL) | format_trade_close_alert() | No direct broadcast | ❌ MISSING |
| Daily recap broadcast | format_daily_recap() | No auto broadcast | ❌ MISSING |
| Public stats | format_winrate() | Only via /winrate cmd | ⚠️ OK |
| FOMO messages | "CUAN! Profit secured!" | In trade_tracker.py | ⚠️ PARTIAL |
| Balance query | get_balance() | Not in unified bot | ❌ MISSING |

---

## 🎯 Phase 1: Competitor Features (Build to Sell)

| Feature | Competitor Proof | Effort | Revenue Impact |
|---------|-----------------|--------|----------------|
| Public /stats with win rate + P&L | SignalBots.ai | 1 day | HIGH |
| Trade result broadcast (TP/SL alerts) | @NIMRASTC_bot | 1 day | HIGH |
| Daily recap auto-broadcast | @vilonaaichanel | 0.5 day | MEDIUM |
| FOMO timer / countdown | SS7Trader | 0.5 day | MEDIUM |
| Multi-timeframe signals (5s-60m) | TradeFather | 1 day | HIGH |

## 🤑 Phase 2: Monetization

| Feature | Model | Effort | Revenue |
|---------|-------|--------|---------|
| Paid VIP via Telegram Stars | Tiered pricing | 2 days | $3K-20K/mo |
| Broker affiliate links | CPA per deposit | 0.5 day | $500-5K/mo |
| Auto-execution via WebSocket | One-click trade | 3 days | High conv. |
| Public results channel | Transparency | 0.5 day | Trust |

## 📋 ORDER OF EXECUTION

1. **Week 1:** Restore lost commands (Phase 0)
2. **Week 2:** Public track record + trade results broadcast (Phase 1)
3. **Week 3:** Multi-timeframe + FOMO timers (Phase 1)
4. **Week 4:** Paid subscription + affiliate program (Phase 2)
