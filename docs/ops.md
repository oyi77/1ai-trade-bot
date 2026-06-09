# Operations Runbook

## Deployment

### Docker Compose (Recommended)

```bash
# Build
docker compose build

# Start all services
docker compose up -d

# Start specific service
docker compose up -d bridge

# View logs
docker compose logs -f

# Stop
docker compose down
```

### Systemd (Linux)

Service files in `deploy/systemd/`:

```bash
# Install service
sudo cp deploy/systemd/tradebot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable tradebot

# Start / Stop / Status
sudo systemctl start tradebot
sudo systemctl stop tradebot
sudo systemctl status tradebot

# View logs
journalctl -u tradebot -f
```

### Manual

```bash
# Activate virtualenv
source .venv/bin/activate

# Start bridge server
tradebot bridge 8082

# Start signal pipeline
tradebot signals

# Start monitoring
tradebot monitor
```

---

## Environment Variables

### Required

| Variable | Description | Example |
|----------|-------------|---------|
| `DERIV_APP_ID` | Deriv application ID | `1234` |
| `DERIV_PAT_TOKEN` | Deriv personal access token | `abc123...` |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token | `123:ABC...` |
| `TELEGRAM_CHAT_ID` | Telegram chat ID | `-100123456` |

### Critical Trading Parameters

| Variable | Default | Description |
|----------|---------|-------------|
| `DERIV_MODE` | `demo` | `demo` or `real` — **ALWAYS start with demo** |
| `BROKER_DRY_RUN` | `True` | Paper trading mode — **set False only for live** |
| `DAILY_TAKE_PROFIT` | `5.0` | Daily profit target (USD) |
| `DAILY_STOP_LOSS` | `-8.0` | Daily loss limit (USD) |

### Tuning

| Variable | Default | Description |
|----------|---------|-------------|
| `ENGINE_CONSENSUS_MIN_VOTES` | `2` | Min engines for consensus signal |
| `ENGINE_CONFIDENCE_THRESHOLD` | `0.5` | Min confidence to emit |
| `SIGNAL_DEDUP_WINDOW` | `60` | Seconds to suppress duplicates |
| `MONITORING_HEARTBEAT_INTERVAL` | `60` | Health check interval (seconds) |

---

## Health Checks

### Endpoints

When `HealthProbe` is running:

| Endpoint | Description |
|----------|-------------|
| `GET /healthz` | Liveness — is the process alive? |
| `GET /readyz` | Readiness — can it serve traffic? |
| `GET /startupz` | Startup — has initialization completed? |

### CLI Health Check

```bash
tradebot health
```

Outputs JSON with per-component status:

```json
{
  "ok": true,
  "status": "OK",
  "checks": [
    {"name": "broker_connectivity", "status": "OK", "latency_ms": 45},
    {"name": "market_data", "status": "OK", "latency_ms": 12},
    {"name": "disk_usage", "status": "OK", "latency_ms": 1}
  ]
}
```

### Status Codes

| Status | Meaning | Action |
|--------|---------|--------|
| `OK` | All checks pass | None |
| `DEGRADED` | Non-critical issue | Monitor, investigate |
| `DOWN` | Critical failure | Immediate action required |

---

## Monitoring

### Prometheus Metrics

When `MONITORING_PROMETHEUS_ENABLED=True`:

```bash
curl http://localhost:8000/metrics
```

Exposed metrics:
- `tradebot_signals_total` — Total signals generated
- `tradebot_trades_total` — Total trades executed
- `tradebot_win_rate` — Current win rate
- `tradebot_latency_seconds` — Processing latency histogram
- `tradebot_engine_votes` — Per-engine vote counts

### Log Files

| File | Content |
|------|---------|
| `logs/auto_signal_publisher.log` | Signal publisher output |
| `logs/vilona_tradefx.log` | Vilona bot activity |
| `logs/phantomfx_bot.log` | PhantomFX bot output |
| `logs/bridge.log` | Bridge server requests |
| `logs/dashboard.log` | Dashboard activity |

### Log Format

Production logs are JSON (newline-delimited):

```json
{"timestamp": "2026-06-09T10:30:00Z", "level": "INFO", "name": "tradebot.pipeline", "message": "Signal processed", "correlation_id": "abc-123"}
```

Set `LOG_FORMAT=console` for human-readable output during development.

---

## Common Operations

### View Current Configuration

```bash
tradebot config
```

### Export Trade History

```bash
tradebot export
# Creates CSV file with all trade records
```

### Run Backtest

```bash
tradebot backtest R_75 Momen 500
# Symbol: R_75, Pattern: Momen, Ticks: 500
```

### Start a Bot

```bash
# Start Vilona bot
tradebot bot start vilona

# Start Stockity bot
tradebot bot start stockity

# Stop a bot
tradebot bot stop vilona
```

### Check System Status

```bash
tradebot status
```

---

## Troubleshooting

### Connection Issues

1. Check `.env` has valid credentials
2. Verify network: `ping api.deriv.com`
3. Check Deriv app ID is registered
4. Review `DERIV_MODE` — must be `demo` or `real`

### No Signals Generated

1. Check engine consensus: `ENGINE_CONSENSUS_MIN_VOTES` — reduce if too strict
2. Check `ENGINE_CONFIDENCE_THRESHOLD` — lower to 0.3 for more signals
3. Verify market data source is reachable: `tradebot test R_75`
4. Check logs for engine errors

### High Memory Usage

1. Check `SIGNAL_HISTORY_SIZE` — default 1000, reduce if needed
2. Check `SIGNAL_QUEUE_MAXSIZE` — default 100
3. Review TieredCache `max_size`

### Bot Not Responding

1. Check if process is running: `ps aux | grep tradebot`
2. Check logs: `tail -f logs/bridge.log`
3. Restart: `tradebot bot stop <name> && tradebot bot start <name>`
4. If persistent: `docker compose restart`

### Database Locked

1. Check for concurrent writes to same SQLite file
2. Ensure `STORAGE_DB_PATH` doesn't conflict between services
3. Use separate DB files per service in Docker

---

## Backup & Recovery

### Database Backup

```bash
# Backup trade history
cp data/tradebot.db data/tradebot.db.backup.$(date +%Y%m%d)

# Backup cognitive patterns
cp data/tradebot.db data/cognitive.db.backup.$(date +%Y%m%d)
```

### Recovery

```bash
# Restore from backup
cp data/tradebot.db.backup.20260609 data/tradebot.db
```

### Rollback

```bash
# Revert to previous version
git log --oneline -5
git checkout <commit-hash>
pip install -e .
docker compose up -d --build
```

---

## Security

### Secrets Management

- **Never** commit `.env` to version control
- Use environment variables in production
- Rotate tokens periodically
- Use `demo` mode for testing

### Network

- Bridge server binds to `0.0.0.0:8082` by default
- Restrict access with firewall rules in production
- Use HTTPS reverse proxy for external access

### Rate Limiting

- Binance: 1200 requests/minute
- Yahoo Finance: 2000 requests/hour
- Deriv: Follow API rate limits
- Internal rate limiter: `AsyncRateLimiter` per source
