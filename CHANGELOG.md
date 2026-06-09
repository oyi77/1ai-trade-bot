# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] — 2026-06-09

### Added
- **Modular package structure** — `tradebot/` package with clean subpackage boundaries
- **11 signal analysis engines** — SMC, FVG, Liquidity, Sweep, Chaos, CRT/TBS, TV, Quant, Hermes Liquidity, Layering, Session Levels
- **Engine auto-discovery** — `Registry.discover()` scans `tradebot.engines` for Engine subclasses
- **Engine consensus** — `EngineConsensus` aggregates signals with weighted voting
- **Signal pipeline** — `SignalPipeline` orchestrates ticks → engines → consensus → middleware → signal
- **Middleware chain** — Logging, RateLimit, Validation, Dedup, RiskCheck middlewares
- **Trade executor** — `TradeExecutor` for order lifecycle management
- **Market data sources** — Binance, Yahoo Finance, Forex (Frankfurter), Stockity with `MarketAggregator` routing
- **Fallback chain** — `FallbackChain` with multi-source cascade (currency-api, CoinGecko, FCS)
- **Bot framework** — `BaseBot` abstract class with lifecycle, command routing, and messaging
- **VilonaBot** — Multi-asset AI-powered FX/commodity signal bot with OmniRoute integration
- **StockityBot** — Proactive binary-options signal dispatcher (14 symbols, 40s scan loop)
- **SubscriptionTradingBot** — Payment-gated trading bot with subscription management
- **Monitoring** — `MetricsCollector`, `HealthProbe` (Kubernetes-compatible HTTP), `TradeTracker` (SQLite-backed)
- **Health service** — `HealthService` with multi-component checks (broker, pipeline, data, disk, DB)
- **Watchdog** — `WatchdogService` with rate-limited alerts and auto-restart
- **Event bus** — In-process `EventBus` (pub/sub, thread-safe, sync+async handlers)
- **Storage layer** — `SQLiteStorage`, `CognitiveDB` (pattern memory), `TieredCache`
- **Structured logging** — JSON formatter, correlation IDs, configurable console/JSON output
- **CLI** — 12+ commands: test, trade, stream, backtest, bridge, signals, health, monitor, analytics, bot, config, version
- **Configuration** — Pydantic Settings with 80+ environment variables across 10 groups
- **Backward compatibility** — Legacy import paths from `scripts/deriv/` emit DeprecationWarning
- **Docker** — Dockerfile and docker-compose.yml for multi-service deployment
- **Comprehensive test suite** — 172+ tests covering models, config, exceptions, pipeline, signals, analytics, patterns, strategies

### Changed
- Migrated from flat scripts/ to modular tradebot/ package structure
- All imports now go through `tradebot.*` namespace

## [0.1.0] — 2026-05-01

### Added
- Initial Deriv WebSocket client
- Basic Momen/Adjacency/Streak pattern analyzers
- DigitMartingaleStrategy
- Tick-by-tick backtest engine
- HTTP signal bridge server
- Telegram notification service
