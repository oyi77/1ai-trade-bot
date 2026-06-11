# Signals — Market Data Sources

**Location:** `tradebot/signals/`
**Entry:** `MarketAggregator` in `market.py`
**Pattern:** `BaseDataSource` ABC → concrete sources → `MarketAggregator` routing

## 8 Data Sources
| Source | File | Routes | Auth |
|--------|------|--------|------|
| Stockity | `stockity.py` | Platform assets (CRYPTO_IDX) | `STOCKITY_FULL_COOKIE` |
| CCXT | `ccxt_source.py` | Crypto (BTC-USD, ETH-USD) | Exchange API keys |
| Binance | `binance.py` | Crypto (fallback) | None (public) |
| MT5 | `mt5_source.py` | Commodities (XAUUSD, USOIL) | MT5 login credentials |
| Yahoo | `yahoo.py` | Everything else | None |
| Forex | `forex.py` | Forex pairs (EURUSD=X) | None |
| Deriv | `deriv_source.py` | Synthetic indices (R_75) | DERIV_APP_ID + token |
| Firebase | `firebase_listener.py` | External signals (CALL/PUT) | Firebase API key |

## Adding a New Source
1. Create file extending `BaseDataSource`
2. Implement `fetch(symbol, interval, count) -> list[OHLCV]`
3. Add instance to `MarketAggregator.__init__()`
4. Add routing in `_select_sources()`
5. Export from `__init__.py`
6. Register in `tradebot/signals/__init__.py`
