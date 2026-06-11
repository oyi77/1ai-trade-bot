# Bots — VilonaBot

**Location:** `tradebot/bots/platforms/vilona/`
**Entry:** `VilonaBot` in `bot.py`
**Pattern:** Mixin-based composition (CommandHandlers, CallbackHandlers, AnalysisHandlers)

## Files
| File | Purpose | Size |
|------|---------|------|
| `bot.py` | Core class: init, lifecycle, Telegram API, update dispatch, `_register_commands()` | 9KB |
| `commands.py` | All command handlers (30+) + `register_vilona_commands()` | 31KB |
| `analysis.py` | AI analysis, mechanical signals, auto-loop, broadcast | 13KB |
| `callbacks.py` | Menu nav, trade/payment/donation callbacks | 4KB |
| `helpers.py` | Constants, WIB time, signal formatting, FOMO phrases | 5KB |

## Rules
- Commands return `str` (HTML). Bot sends via `_tg_send()`. Response is the return value.
- Menu callbacks use `cmd:*` (execute command), `menu:*` (navigate), `__url__` (URL button)
- New commands: add handler method + register in `_register_commands()` dict + add button in `menu.py`
- Donor-locked features: check `members_service.get_member()` for `tier == "donor"`
- Always include FOMO CTA for locked features
