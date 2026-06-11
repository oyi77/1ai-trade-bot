# Services — Business Logic Layer

**Location:** `tradebot/services/`
**Pattern:** Module-level functions + optional classes. Singleton instances preferred.

## Key Modules
| Module | Purpose | Pattern |
|--------|---------|---------|
| `menu.py` | Inline keyboard menus (9 menus, 49+ callbacks) | Static definitions |
| `consensus_service.py` | Engine consensus + TieredCache (120s TTL) | Cache-aside |
| `signal_service.py` | Signal feed data layer (absorbed from scripts/) | Module functions |
| `trade_tracker_service.py` | Trade history, stats, daily recap | Module functions |
| `members_service.py` | Member/donor DB access (SQLite) | Module functions |
| `payment.py` | Tripay + Duitku payment gateway | Class (`PaymentService`) |
| `health.py` | System health checks | `check_all()` function |
| `telegram.py` | Telegram message sending (HTTP API) | Class (`TelegramService`) |

## Rules
- Services NEVER import from `scripts/` or `members/` packages directly
- Use `tradebot.config.settings` for all configuration
- Async methods for I/O operations
- All exceptions must be logged (no silent `except: pass`)
