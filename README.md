# 🤖 Multi-Exchange Trader

A modular trading bot supporting **Paper Trading** (simulation), **Frontest** (demo account), and **Real Trading** (live) across multiple exchanges — Exness, Binance, Bybit, OKX, and Ostium DEX.

## ✨ Features

| Feature | Description |
|---------|-------------|
| 📘 **Paper Trading** | Pure simulation - no account needed |
| 📗 **Frontest** | Demo account with real market data |
| 📕 **Real Trading** | Live trading with real money |
| **Multi-Provider** | Exness, Binance, Bybit, OKX, Ostium DEX |
| **Strategies** | XAU Hedging, Grid, Trend Following |
| **Risk Management** | Automatic safety checks, lot validation |
| **TUI** | Rich-based Terminal UI with live dashboard |
| **CLI** | Traditional command line |
| **Modular** | Easy to add new interfaces |

---

## 🚀 Quick Start

### TUI Mode (Default) - Interactive Wizard
```bash
# Launch interactive setup wizard
python main.py              # Uses TUI by default
python main.py -i tui       # Explicit TUI mode
```

### CLI Mode - Arguments Only
```bash
# Use command line arguments (no wizard)
python main.py -i cli --mode paper --symbol XAUUSDm --lot 0.02

# Auto-start without confirmation
python main.py -i cli -y --mode paper
```

### 1. Paper Trading (No Account Needed)
```bash
# Interactive wizard (recommended)
python main.py -i tui

# CLI mode with auto-start
python main.py -i cli -y

# CLI with custom parameters
python main.py -i cli --mode paper --lot 0.01 --balance 500 -y
```

### 2. Frontest (Demo Account)
```bash
# Exness demo
export EXNESS_TOKEN="your_jwt_token"
python main.py -i tui --mode frontest --provider exness

# Binance testnet
export EXCHANGE_API_KEY="..."
export EXCHANGE_API_SECRET="..."
python main.py --mode frontest --provider ccxt --exchange binance
```

### 3. ⚠️ Real Trading
```bash
# ⚠️ REAL MONEY AT RISK!
python main.py --mode real --provider exness
```

---

## 📖 Documentation

- **[USAGE.md](USAGE.md)** - Complete usage guide with examples
- **[docs/](docs/)** - Additional documentation

---

## 🎯 Trading Modes

```
┌─────────────────────────────────────────────────────────────────┐
│  📘 PAPER     │  📗 FRONTEST      │  📕 REAL                    │
├─────────────────────────────────────────────────────────────────┤
│  Simulation   │  Demo account     │  Real money ⚠️               │
│  No broker    │  Real data        │  Real data                   │
│  Virtual $    │  No risk          │  Real losses possible        │
│  Test strategy│  Test execution   │  Production                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 🔌 Supported Providers

| Provider | Type | Auth | Status |
|----------|------|------|--------|
| **Exness** | Forex/CFDs | JWT Token | ✅ Ready |
| **CCXT** | Crypto | API Key + Secret | ✅ Ready |
| **Ostium** | DEX | Private Key | ✅ Ready |
| **Other CFDs** | Forex | - | 🚧 Coming soon |

---

## 🛡️ Safety First

### Risk Warnings

⚠️ **Before using REAL mode:**
1. Test thoroughly in **PAPER** mode
2. Validate in **FRONTEST** mode with demo
3. Start with minimum lot size (0.01)
4. Never risk more than you can afford to lose

### Lot Size Safety ($100 Account)

| Lot | Risk | Status |
|-----|------|--------|
| 0.005 | $25 (25%) | ✅ Safe |
| 0.01 | $50 (50%) | ⚠️ Max |
| 0.1 | $500 (500%) | ❌ Deadly |

---

## 📦 Installation

```bash
# Clone repository
git clone <repo-url>
cd trading-bot

# Install dependencies
pip install -r requirements.txt

# Run
python main.py
```

---

## 🎮 Interactive Mode

```bash
$ python main.py

    ╔═══════════════════════════════════════════════════╗
    ║              🤖 TRADING BOT SYSTEM                ║
    ╚═══════════════════════════════════════════════════╝

📊 STEP 1: SELECT TRADING MODE

[1] 📘 PAPER    - Pure simulation
[2] 📗 FRONTEST - Demo account  
[3] 📕 REAL     - Real money ⚠️

Select mode [1-3]: 2
...
```

---

## 📁 Project Structure

```
trading_bot/
├── core/              # Core engine
├── exchange/          # Exchange providers
│   ├── exness_web.py  # Exness Web API
│   ├── ccxt.py        # CCXT integration
│   └── paper_trading.py
├── strategy/          # Trading strategies
├── utils/             # Utilities
│   └── auth.py        # Authentication manager
└── risk/              # Risk management

main.py         # Main CLI entry point
USAGE.md              # Detailed usage guide
```

---

## 🔐 Authentication

### Exness
```bash
export EXNESS_TOKEN="jwt_token"
export EXNESS_ACCOUNT_ID="413461571"
export EXNESS_SERVER="trial6"
```

### CCXT (Binance, Bybit, etc)
```bash
export EXCHANGE_NAME="binance"
export EXCHANGE_API_KEY="..."
export EXCHANGE_API_SECRET="..."
```

### Ostium DEX
```bash
export OSTIUM_PRIVATE_KEY="0x..."
export OSTIUM_RPC_URL="https://arb1.arbitrum.io/rpc"
```

---

## 🧪 Testing

The trading bot includes comprehensive unit and integration tests.

```bash
# Run all tests
python run_tests.py

# Run with verbose output
python run_tests.py --verbose

# Run specific test file
python -m pytest tests/test_strategy.py -v

# Run with coverage
python run_tests.py --coverage
```

**Test Coverage: 85+ tests passing**

| Category | Tests |
|----------|-------|
| Authentication | 12 |
| Exchange Providers | 28 |
| Strategies | 18 |
| Configuration | 12 |
| Integration | 15 |

---

## 📝 License

MIT License - See LICENSE file

---

**Disclaimer**: Trading involves significant risk. This bot is for educational purposes. Past performance does not guarantee future results. Never trade with money you can't afford to lose.
