# ADR 002: Configuration Management

## Status
Accepted

## Context
The consolidated trading bot requires a flexible configuration system supporting multiple environments (paper, testnet, real), multiple exchange types (CEX, DEX), and multiple chains. Configuration comes from four sources with increasing priority.

## Decision

### Layered Configuration
Configuration is loaded in this priority order (last wins):
1. `config/default.yml` - Base defaults shipped with the bot
2. `config/profiles/<profile>.yml` - Profile-specific overrides (selected via `--mode`)
3. Environment variables - System/env overrides (all uppercase, underscore separators)
4. CLI arguments - Highest priority, single-session overrides

### Config Sections

| Section | Purpose |
|---------|---------|
| trading | Mode, symbol, lot, leverage, positions |
| exchange | Provider, chain, RPC, CEX/DEX settings |
| wallet | Key source, derivation path |
| risk | Drawdown limits, position sizing, circuit breaker |
| data_providers | Market data source selection |
| logging | Level, file output, format |

### Profile Inheritance
Profiles use an `extends` key to inherit from `default.yml`. Only overridden fields need to be specified. The loader merges the base defaults with profile overrides, then applies env var overrides and CLI args on top.

### Backward Compatibility
All existing env var names continue to work:
- `EXNESS_TOKEN`, `EXCHANGE_API_KEY`, `EXCHANGE_API_SECRET`
- `OSTIUM_PRIVATE_KEY`, `OSTIUM_RPC_URL`
- `BIRDEYE_API_KEY`, `PVT_KEY_*` (per-chain private keys)

## Consequences

### Positive
- Flexible multi-environment configuration
- Backward compatible with existing env var usage
- Clear layering with well-defined precedence
- Minimal profile files (only overrides needed)

### Negative
- More files to manage in the config/ directory
- Profile inheritance concept requires documentation

### Mitigation
Only 3 profiles are needed (paper, frontest, real). The `--help` output documents all config options and layering.

## References
- Config files: `config/default.yml`, `config/profiles/*.yml`
- ADR 001: Hexagonal Architecture
