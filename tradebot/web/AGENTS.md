# Web — FastAPI Server

**Location:** `tradebot/web/`
**Entry:** `server.py` (FastAPI app, port 9090)
**Pattern:** Route handlers + static templates + API routers

## Files
| File | Purpose |
|------|---------|
| `server.py` | Main FastAPI app — 55 routes (admin, public, webhook, health) |
| `monitoring_api.py` | 6 monitoring endpoints (engines, brokers, metrics, trades, status, errors) |
| `bridge_api.py` | 8 bridge endpoints (MT5 EA signal polling) |
| `public_dashboard.py` | Public dashboard helpers (daily recap, mapping, donors) |
| `templates/` | HTML templates (admin monitoring, public dashboard EN/ID/bilingual) |

## Routes Summary
- `/admin*` — Admin dashboard (session auth required)
- `/dashboard*` — Public signal dashboard (no auth)
- `/api/bridge/*` — MT5 EA signal polling (API key auth)
- `/api/monitoring/*` — System monitoring (no auth, internal)
- `/api/webhook/*` — External webhooks (snapshot, Tripay payment)
- `/health` — Health check

## Rules
- Monitoring endpoints intentionally have NO auth (designed for loopback consumption)
- Bridge endpoints use API key auth (`api_key` query param)
- New public routes: no auth required. Admin routes: session auth required.
- Tripay webhook verifies HMAC-SHA256 signature before processing
