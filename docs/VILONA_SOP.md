# VILONA SYSTEM — STANDARD OPERATING PROCEDURE (HARD RULES)

> **Ini SOP kita. Jangan dilanggar.**

---

## 1. CORE TRUTH

Vilona adalah **deterministic SMC engine**, bukan AI probabilistic.
- Engine Logic: Pure SMC (Liquidity Sweep, Displacement, FVG, Order Block, Breaker Block)
- Timeframe: M15 (trigger) — H1/H4 structure — D1 macro
- Output: **Market Pulse** (analysis only) atau **Active Signal** (actionable entry/SL/TP)

---

## 2. QUALITY GATE — ASSET ROUTING

| Asset Class | Examples | Killzone Rule | Data Source |
|---|---|---|---|
| Forex | EURUSD, GBPUSD | **London + NY ONLY** | No live API (broker only) |
| Metals | XAUUSD, XAGUSD | **London + NY ONLY** | gold-api.com |
| Crypto | BTCUSD, ETHUSD | **24/7 — BYPASS time gates** | Binance public API |

### LARANGAN DATA:
- 🔴 **JANGAN PAKE YAHOO FINANCE** — harga beda. Udah dibuktikan berkali-kali.

### Hard Rule:
- **Forex & Metals**: Jika diluar London & NY killzone → HOLD.
- **Crypto**: Tidak ada block berdasarkan time. SMC murni yang menentukan.
- **S-TIER setup**: Override volume constraint jika MTF Matrix 90-100% aligned.

Killzone definitions (WIB):
- London: 14:00-18:00
- New York: 19:00-23:00

---

## 3. PIPELINE ARCHITECTURE

```
Data Sources (Yahoo Finance / gold-api.com / Deriv WS)
    │
    ▼
OHLCV Cache (yf-cache) — M15/H1/H4/D1
    │
    ▼
Signal Generation (analysis.py / vilona_tradefx_handler.py)
    ├─ S-TIER Zone (Priority #1) — Breaker Blocks + FVG + Trend
    ├─ Quant+FVG (Priority #2) — Volume profile + FVG alignment
    └─ AI Consensus (Priority #3) — Multi-model voting (DeepSeek, Mistral, dll.)
        ├─ DUAL (≥2 models agree) → Quality gate → S-TIER cross-check → PUSH
        └─ SOLO (1 model) → S-TIER cross-check → Grok news check → PUSH
    │
    ▼
Quality Gate
    ├─ Confiance ≥ 65%
    ├─ RR ratio ≥ 1:1.5
    ├─ S-TIER cross-check: JIKA kontradiksi → BLOCK atau crash confidence
    ├─ Grok News: HIGH impact berlawanan → BLOCK
    └─ Entry distance check: < 50% SL distance dari zone
    │
    ▼
Signal Bridge (port 8765)
    ├─ signal_feed.json — untuk historical / tracking
    └─ ea_signal.json — untuk EA Executor
    │
    ▼
EA Executor (PAPER MODE — tidak ada live broker)
    ├─ Position management (SL/TP monitoring)
    ├─ Slippage guard (per-symbol, ATR-aware)
    └─ Trade Result → handler broadcast via Telegram
    │
    ▼
Telegram Broadcast
    ├─ Signal subscribers (tier-based)
    └─ PAID/GROUP channels
```

---

## 4. SIGNAL GENERATION RULES

### 4.1 S-TIER Zone Detection
1. Scan: Breaker Blocks → Order Blocks → FVG → Trend alignment
2. Score: min 3.5 untuk publish. S-TIER = ≥ 6.0
3. CONTOH: jika score 6+ (triple confluence: breaker + FVG + trend) → 💀 S-TIER

### 4.2 Mechanical (Quant+FVG)
- Quant match ≥ 20 (naik dari 15 — fix #4)
- FVG confidence ≥ 0.35 (naik dari 0.20 — fix #4)
- Green/red pattern match ≥ 50%
- MTF structure check: H4/D1 trend tidak boleh bertentangan

### 4.3 AI Consensus
- **DUAL**: Minimal 2 model setuju. Quality gate + S-TIER cross-check.
- **SOLO**: Cuma 1 model available → mechanical harus setuju. Grok news dicek.
- **Quality gate**: Free user tidak boleh dapet sinyal tanpa mechanical confirmation.
- **Prioritas**: DeepSeek > Mistral > Cohere > Gemini > Kimi (auto-fallback)

### 4.4 TP/SL Calculation
- **XAUUSD**: ATR-based. Fallback: 30 pip SL, 60 pip TP.
- **BTCUSD**: ATR-based. Fallback NONE — harus pake ATR.
- **USOIL**: ATR-based.
- **TP2**: Structure-based (extended half beyond TP1). Max 200 pip dari entry.

---

## 5. EXECUTION RULES (EA EXECUTOR)

1. **Paper mode only** — tidak ada MT5/Deriv live integration
2. **Slippage guard**: bandingkan live price vs signal entry. Batas: 15 pip (per-symbol)
3. **Max 1 open position** — antri, jangan stacking
4. **Confidence floor**: 65% minimum. RR: min 1:1.5
5. **Signal filter**: skip jika duplicate fingerprint
6. **Symbol-aware pricing**: JANGAN pakai XAUUSD price buat USOIL/BTCUSD

---

## 6. GIT WORKFLOW (VILONA RULES — BUKAN PROJECT RULES)

Kita punya aturan sendiri, bukan dari AGENTS.md project.

### Commit Format
```
<type>: <subject>
<body> — explain WHY, not WHAT
```

Types: `fix:`, `feat:`, `chore:`, `refactor:`, `docs:`

### Split
- ≤3 files → 1-2 commits. Bedain critical fix vs minor adjustment.

### TIDAK PERLU:
- ❌ Co-author trailer — SOP kita, bukan project standard
- ❌ "Ultraworked with Sisyphus" — ga relevan
- ❌ Wrapping body 72 chars — keep it readable, ga usah kaku
- ❌ Ngikutin aturan AGENTS.md project lain — kita punya aturan sendiri

### LARANGAN:
- ❌ Jangan commit database files, logs, .env, atau cache
- ❌ Jangan commit signal_feed.json, ea_state.json, atau runtime data
- ❌ Jangan commit kode yang belum di-test
- ❌ Jangan commit tanpa verify syntax

---

## 7. EMERGENCY PROCEDURES

### 7.1 EA Executor Crash
```bash
sudo systemctl restart ea-executor.service
tail -f logs/ea_executor.log
```

### 7.2 Signal Bridge Down
```bash
sudo systemctl restart vilona-tradefx-bridge.service
```

### 7.3 Handler (Bot) Down
```bash
sudo systemctl restart vilona-tradefx-bot.service
```

### 7.4 Worker Down
```bash
sudo systemctl restart vilona-worker.service
```

### 7.5 Missing Signals / No Broadcast
1. Check `logs/vilona_tradefx.log` — "BLOCKED" atau "HOLD"?
2. Check `data/vilona_tradefx/ea_signal.json` — ada signal terbaru?
3. Check `logs/ea_executor.log` — diproses atau di-skip?
4. Check API keys: Grok, DeepSeek, OpenAI — expired?

### 7.6 Zero Downtime Principle
- Restart service, bukan kill process.
- JANGAN restart semua service sekaligus — satu per satu.
- Stack: Handler → Bridge → EA → Worker

---

## 8. AUTOMATED SAFETY (CIRCUIT BREAKERS)

- **Dedup window**: 5400s (90 menit) — sesuai M15 siklus
- **Daily signal limit**: 5 mechanical signals per pair per hari
- **Entry distance**: skip sinyal jika price > 50% SL distance dari zone
- **Circuit breaker**: reset otomatis tiap hari
- **Slippage abort**: >15 pip dari live price → cancel

---

## 9. DASHBOARD & MONITORING

| Service | Port | URL | Status |
|---|---|---|---|
| Dashboard Server | 8768 | http://localhost:8768 | ✅ |
| Signal Bridge | 8765 | http://localhost:8765 | ✅ |
| EA Executor | - | systemd: ea-executor | ✅ |
| Public Dashboard | 6969 → CF | phantomfx.aitradepulse.com | ✅ |
| Admin Panel | 9090 | http://localhost:9090 | ✅ |

Check command:
```bash
systemctl list-units --type=service --state=running | grep -iE "(vilona|ea-)"
```

---

## 10. NON-NEGOTIABLE (PELANGGARAN = ROLLBACK)

1. **S-TIER direction**: Breaker block — arah harus terbalik dari OB. Jangan dibalikin.
2. **Killzone rule**: Forex/metals TIDAK boleh trade di Asian session.
3. **Crypto bypass**: BTCUSD tidak boleh di-block oleh time gate.
4. **Mechanical confirmation**: Free user TIDAK boleh dapet sinyal dari AI solo tanpa mechanical check.
5. **ATR for non-XAUUSD**: BTCUSD dan USOIL WAJIB pake ATR, bukan hardcode pip.
6. **Slippage guard**: Harus per-symbol, jangan hardcode pip size.

> **Siapa yang melanggar — fixnya di-revert, SOP di-update.**

---

## 11. AUTONOMOUS LEARNING LOOP 🧠

> **Setiap SL = Pelajaran. Setiap TP = Pola. Semua otonom — zero user command.**

### 11.1 Alur Belajar

| Event | Aksi | Output |
|---|---|---|
| **SL HIT** 🛑 | `learn_from_sl()` → root cause analysis | `data/vilona_tradefx/lessons.json` |
| **TP HIT** 🎯 | `learn_from_tp()` → simpan pola entry | `data/vilona_tradefx/winning_patterns.json` |

### 11.2 Root Cause Analysis (SL)

Setiap SL kena, sistem auto-analisa kenapa:
- **SL_TOO_TIGHT**: SL kurang dari 20 pip — gak kasih ruang napas
- **WRONG_DIRECTION**: Entry di top (BUY) atau bottom (SELL) — price langsung berlawanan
- **LOW_CONFIDENCE**: Confidence <40% — seharusnya skip sinyal
- **RISK_REWARD**: RR <1:1 atau >5:1
- **WEAK_GRADE**: Grade C/D — sinyal lemah, perlu filter lebih ketat

### 11.3 Winning Pattern (TP)

Setiap TP kena, sistem simpan:
- Action + Symbol + Grade + Confidence
- Entry price + SL + TP
- Time context (hour_wib)
- Source (channel-auto / stier / mechanical)

### 11.4 Files

| File | Path | Purpose |
|---|---|---|
| Engine | `scripts/learning_loop.py` | All learning logic |
| Integration | `scripts/vilona_tradefx_handler.py` | Auto-called in scan loop |
| Lesson DB | `data/vilona_tradefx/lessons.json` | SL lessons |
| Pattern DB | `data/vilona_tradefx/winning_patterns.json` | TP patterns |

### 11.5 Trigger

- **Otomatis** — berjalan di dalam handler scan loop setiap ada trade closed
- **Zero user command** — gak perlu dikasih perintah
- **Cron review**: `weekly-learning-review` tiap Minggu 21:00 WIB
- **Query manual**: `get_learning_summary()` dari `learning_loop`

### 11.6 Git & Brain

- Commit: `3938b90` — `feat: autonomous learning loop`
- Brain: `drawer_trading_general_62b739125c9a918c99ff2f6b` (BK Brain `/brain/add`)

> **PELANGGARAN**: Jangan matikan LEARNING_LOOP flag. Jangan bypass learning call.
> Setiap trade yang closed WAJIB dipelajari — SL untuk diperbaiki, TP untuk diulang.

---

*Version: 1.0 — 12 June 2026*
*Owner: Vilona Engineering*
