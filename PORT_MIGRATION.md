# Port Migration: 9090 → 8888

## Problem
Port 9090 was already in use by 1ai-hub service. The unified trading bot needed a different port.

## Solution
Changed the default port to **8888** — a memorable, unused port in the cf-router configuration.

## Changes Made

### 1. Code Updates
- **tradebot/app.py**
  - Line 31: Changed default port from 9090 to 8888 in `__init__`
  - Line 92: Changed default port in argparse from 9090 to 8888
  - Line 13: Updated usage documentation to show port 8888

- **tradebot/web/__main__.py**
  - Line 10: Changed default port from 9090 to 8888

### 2. Cloudflare Router
Added to `~/.cloudflare-router/mappings.yml`:
```yaml
- subdomain: tradebot
  port: 8888
  description: 1ai-trade-bot Dashboard
  protocol: http
  created_at: '2026-06-10T00:00:00.000Z'
  updated_at: '2026-06-10T00:00:00.000Z'
  enabled: true
```

## Available Ports Analysis

```
Checked: ~/.cloudflare-router/mappings.yml

Currently used ports:
  3000, 3001, 3002, 3003, 3010, 3100, 4040, 7070, 8080, 8502, 8766, 8787,
  20131, 20241, 40803, 40876, 44685, 45927

Selected port: 8888 (memorable, easy to remember, not in use)

Alternative available ports:
  8000-8787, 8789-9999, 9010+
```

## Deployment Instructions

### Local Development
```bash
python -m tradebot --port 8888
# Or use default (already 8888)
python -m tradebot
```

### Via Cloudflare Tunnel
```bash
# The mapping is already added to cf-router
# Access via: https://tradebot.{your-domain}.com
```

### Custom Port (if needed)
```bash
python -m tradebot --port 9999   # Override default
python -m tradebot --host 127.0.0.1 --port 8888
```

## Testing
```bash
✅ 810 tests passing
✅ All imports working
✅ Default port verified as 8888
✅ Custom port override functional
```

## Impact
- ✅ No breaking changes
- ✅ Backward compatible (can still specify custom ports)
- ✅ Clear documentation
- ✅ Registered in cf-router for easy access

## Commit
- **Hash**: 539a9a7
- **Message**: chore: change default port from 9090 to 8888 (avoid conflict with 1ai-hub)
