# ADR 003: Multi-Chain Wallet Management

## Status
Accepted

## Context
The consolidated trading bot needs a unified wallet management system supporting multiple EVM-compatible blockchains (BSC, Ethereum, Polygon, Arbitrum). The IWalletManager interface has been defined at `trading_bot/core/interfaces/wallet_manager.py` with methods for address derivation, balance queries, transaction signing, and wallet import/export.

## Decision

### Interface
The `IWalletManager` ABC defines the contract:
- `get_address() -> str`: Return primary wallet address
- `get_balance(token="", chain="") -> dict`: Query on-chain balance (returns balance, decimals, symbol)
- `sign_transaction(tx_data: dict) -> str`: Sign and return raw hex transaction
- `export_wallet(password: str) -> dict`: Export encrypted keystore
- `import_wallet(wallet_data: dict, password: str) -> bool`: Import from keystore

### Storage Model
- **Primary**: Environment variables (`PVT_KEY_BSC`, `PVT_KEY_ETH`, `PVT_KEY_POLYGON`, `PVT_KEY_ARBITRUM`)
- **Secondary**: Encrypted JSON keystore at `~/.trading_bot/wallets/` protected by PBKDF2-derived key

### Key Derivation
All EVM chains use secp256k1 keys with chain-specific BIP-44 paths:
| Chain | Derivation Path |
|-------|----------------|
| Ethereum | m/44'/60'/0'/0/0 |
| BSC | m/44'/9006'/0'/0/0 |
| Polygon | m/44'/966'/0'/0/0 |
| Arbitrum | m/44'/9001'/0'/0/0 |

### Security Model
- Keys NEVER logged or stored in YAML configuration
- Encrypted at rest via password-derived key (PBKDF2 with 100K+ iterations)
- Keys held in memory only during signing operations
- No plaintext key material in any file

### Backward Compatibility
- Support importing from dex-trader's `wallet_settings.json` format
- Support reading legacy env var names as aliases

## Consequences
- **Positive**: Unified wallet interface across all 4 EVM chains, secure key storage, backward compatible
- **Negative**: Additional complexity for key management, users must set env vars per chain
- **Mitigation**: Migration script handles dex-trader import, clear documentation for env var setup

## References
- IWalletManager: trading_bot/core/interfaces/wallet_manager.py
- BIP-44: https://github.com/bitcoin/bips/blob/master/bip-0044.mediawiki
- Dex-trader: 1ai-dex-trader/configs/settings.json