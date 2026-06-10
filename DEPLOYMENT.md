# 1ai-trade-bot Deployment & Operations

Complete guide for production deployment, process management, and Cloudflare tunnel setup.

## Quick Start

### 1. Start the Bot with PM2

```bash
# Start via ecosystem config
cd ~/projects/1ai-trade-bot
pm2 start ecosystem.config.js

# Or restart if already running
pm2 restart 1ai-trade-bot

# Check status
pm2 status
pm2 logs 1ai-trade-bot

# Monitor real-time
pm2 monit
```

### 2. Verify the Bot is Running

```bash
# Check listening port
# Port is now 8889 (changed from 8888 to avoid Docker SearXNG conflict)
lsof -i :8889
netstat -tuln | grep 8889

# Access web dashboard (local)
curl http://localhost:8889/

# Access via public domain (aitradepulse.com)
curl https://tradebot.aitradepulse.com/
```

## Process Management Options

### Option A: PM2 (Recommended - Lightweight)

- Port: 8889 (changed from 8888; now from 9090 originally)
- Domain: tradebot.aitradepulse.com
**Already configured:**
- Process name: `1ai-trade-bot`
- Port: 8888
- Memory limit: 500M
- Auto-restart on crash
- Logged to `/var/log/tradebot/`

**Essential PM2 Commands:**
```bash
# Start/restart/stop
pm2 start ecosystem.config.js
pm2 restart 1ai-trade-bot
pm2 stop 1ai-trade-bot

# View logs
pm2 logs 1ai-trade-bot
pm2 logs 1ai-trade-bot --lines 100
pm2 logs 1ai-trade-bot --err

# Process info
pm2 info 1ai-trade-bot
pm2 list

# Save config (survives reboots via systemd)
pm2 save

# Auto-startup on boot
pm2 startup systemd -u openclaw --hp /home/openclaw
```

**Logs:**
- Combined: `/var/log/tradebot/combined.log`
- Error: `/var/log/tradebot/error.log`
- Output: `/var/log/tradebot/out.log`

### Option B: systemd (Traditional - Fallback)

A standalone systemd service as backup to PM2.

**Already configured:**
- Service file: `/etc/systemd/system/1ai-trade-bot.service`
- Enabled on boot
- Resource limits: 512M RAM, 80% CPU
- Security: read-only system, no new privileges

**Essential systemd Commands:**
```bash
# Status and control
sudo systemctl status 1ai-trade-bot
sudo systemctl start 1ai-trade-bot
sudo systemctl stop 1ai-trade-bot
sudo systemctl restart 1ai-trade-bot

# Auto-start on boot
sudo systemctl enable 1ai-trade-bot
sudo systemctl disable 1ai-trade-bot

# View logs (journalctl)
sudo journalctl -u 1ai-trade-bot -f           # Follow
sudo journalctl -u 1ai-trade-bot --lines 100  # Last 100
sudo journalctl -u 1ai-trade-bot -p err       # Errors only

# Reload systemd config (after editing .service)
sudo systemctl daemon-reload
```

### Switch Between PM2 and systemd

**Use PM2 (default):**
```bash
# Stop systemd service, use PM2
sudo systemctl stop 1ai-trade-bot
pm2 start ecosystem.config.js
pm2 save
```

**Use systemd (fallback):**
```bash
# Stop PM2, use systemd
pm2 stop 1ai-trade-bot
pm2 delete 1ai-trade-bot
sudo systemctl start 1ai-trade-bot
```

## Cloudflare Tunnel Setup

The bot is registered in Cloudflare router and accessible via domain.

### Current Configuration

```yaml
# ~/.cloudflare-router/mappings.yml
- subdomain: tradebot
  port: 8888
  description: 1ai-trade-bot Dashboard
  protocol: http
  enabled: true
```

### Access the Bot

- **Local (direct)**: `http://localhost:8888`
- **Via Cloudflare tunnel**: `https://tradebot.{domain}.com`
- **Health check**: `https://tradebot.{domain}.com/health`

### How Cloudflare Tunnel Works

1. **Local bot** runs on port 8888
2. **cf-router** detects bot is listening
3. **Cloudflare tunnel** proxies traffic from domain to localhost:8888
4. **Users** access via `https://tradebot.{domain}.com`

```
Internet User
    ↓
HTTPS: tradebot.{domain}.com
    ↓
Cloudflare Tunnel (proxied)
    ↓
localhost:8888 (local bot)
```

### Verify Tunnel is Working

```bash
# Check Cloudflare router status
systemctl status cf-router

# Check bot is listening
lsof -i :8888

# Test local access
curl http://localhost:8888/health

# Check Cloudflare mappings
cat ~/.cloudflare-router/mappings.yml | grep -A 5 tradebot
```

## Configuration Files

### ecosystem.config.js (PM2 Config)
```javascript
// Define process, arguments, environment, resource limits
module.exports = {
  apps: [
    {
      name: "1ai-trade-bot",
      script: "/home/linuxbrew/.linuxbrew/bin/python3",
      args: "-m tradebot --host 0.0.0.0 --port 8888",
      cwd: "/home/openclaw/projects/1ai-trade-bot",
      // ... resource limits, logging, restart policy ...
    },
  ],
};
```

**Edit to change:**
- Port: modify `--port 8888`
- Host: modify `--host 0.0.0.0`
- Memory: change `max_memory_restart: "500M"`
- Restart policy: change `autorestart` or `watch`

Then: `pm2 start ecosystem.config.js && pm2 save`

### 1ai-trade-bot.service (systemd Config)
```ini
# Define systemd service for bot
[Unit]
Description=1ai-trade-bot — Unified Trading Platform
After=network.target

[Service]
Type=simple
User=openclaw
ExecStart=/home/linuxbrew/.linuxbrew/bin/python3 -m tradebot --host 0.0.0.0 --port 8888
Restart=on-failure
RestartSec=10
MemoryMax=512M
CPUQuota=80%

[Install]
WantedBy=multi-user.target
```

**Edit to change:**
- Port: modify `--port 8888`
- Memory: change `MemoryMax=512M`
- Restart delay: change `RestartSec=10`

Then: `sudo systemctl daemon-reload && sudo systemctl restart 1ai-trade-bot`

## Environment Variables

All env vars from `.env` are automatically loaded:

```bash
cat ~/projects/1ai-trade-bot/.env | head -20
```

**Key vars:**
- `TELEGRAM_BOT_TOKEN` — Bot API token
- `ADMIN_USER_IDS` — Comma-separated admin Telegram IDs
- `DERIV_API_TOKEN` — Deriv account token
- `STOCKITY_*` — Stockity credentials
- `OPENAI_API_KEY`, `DEEPSEEK_API_KEY`, `GOOGLE_API_KEY` — LLM keys

## Health Checks

### Local Health Check
```bash
curl http://localhost:8888/health
```

**Expected response:**
```json
{
  "status": "healthy",
  "timestamp": "2026-06-10T15:53:00Z",
  "services": {
    "telegram_bot": "connected",
    "web_server": "running",
    "database": "connected"
  }
}
```

### Systemd Health Check
```bash
sudo systemctl is-active 1ai-trade-bot
# Output: active or inactive
```

### PM2 Health Check
```bash
pm2 info 1ai-trade-bot | grep status
# Output: online or errored
```

## Troubleshooting

### Bot Not Starting (PM2)

**Check logs:**
```bash
pm2 logs 1ai-trade-bot --err
pm2 info 1ai-trade-bot
```

**Common issues:**
- **Port 8888 already in use**: `lsof -i :8888` → kill process or use different port
- **Missing dependencies**: `pip install -e . --break-system-packages`
- **Python not found**: Check `ecosystem.config.js` script path matches `which python3`

**Fix & restart:**
```bash
pm2 stop 1ai-trade-bot
# Fix the issue
pm2 restart 1ai-trade-bot
```

### Bot Not Starting (systemd)

**Check logs:**
```bash
sudo journalctl -u 1ai-trade-bot -f
```

**Common issues:**
- **Permission denied**: User `openclaw` must own `/home/openclaw/projects/1ai-trade-bot`
- **Port in use**: Change port in `.service` file, then `systemctl daemon-reload`

**Fix & restart:**
```bash
sudo systemctl status 1ai-trade-bot  # See error
# Fix the issue
sudo systemctl restart 1ai-trade-bot
```

### Cloudflare Tunnel Not Working

**Check tunnel is running:**
```bash
systemctl status cf-router
curl http://localhost:8888/health  # Test local access first
```

**Check mappings:**
```bash
grep tradebot ~/.cloudflare-router/mappings.yml
```

**Restart tunnel:**
```bash
systemctl restart cf-router
```

**Test from outside:**
```bash
# From another machine
curl https://tradebot.{domain}.com/health
```

## Restart on System Reboot

### Via PM2 (Automatic)
PM2 is configured to resurrect all processes on system boot:
1. `pm2 save` — saves process list
2. systemd service `pm2-openclaw` — starts PM2 on boot
3. PM2 resurrects all saved processes

Verify with:
```bash
systemctl status pm2-openclaw
pm2 save  # Always save after changes
```

### Via systemd (Automatic)
The `1ai-trade-bot.service` is enabled on boot:
```bash
systemctl is-enabled 1ai-trade-bot
# Output: enabled
```

Both PM2 and systemd are configured, so the bot will auto-start even if the system reboots.

## Monitoring

### Real-time CPU/Memory
```bash
pm2 monit
```

### Process History
```bash
pm2 list  # Shows restart count, uptime
```

### Access Logs (Cloudflare)
```bash
# Check cf-router for request logs
tail -f /var/log/cf-router.log 2>/dev/null || echo "Logs location varies by cf-router version"
```

## Deployment Checklist

- [x] Port 8888 configured (not 9090 — 1ai-hub conflict)
- [x] PM2 ecosystem.config.js created
- [x] systemd 1ai-trade-bot.service created
- [x] PM2 auto-startup enabled via systemd
- [x] yfinance dependency added to pyproject.toml
- [x] Bot installed with `pip install -e .`
- [x] Bot running on port 8888 (verified with `lsof`)
- [x] Cloudflare router mappings updated (subdomain: tradebot, port: 8888)
- [x] Health check responding
- [x] Logs directory created and working (`/var/log/tradebot/`)

## Quick Reference

| Task | Command |
|------|---------|
| Start bot | `pm2 start ecosystem.config.js` |
| Stop bot | `pm2 stop 1ai-trade-bot` |
| Restart bot | `pm2 restart 1ai-trade-bot` |
| View logs | `pm2 logs 1ai-trade-bot` |
| Monitor | `pm2 monit` |
| Health check | `curl http://localhost:8888/health` |
| Cloudflare access | `curl https://tradebot.{domain}.com` |
| Save PM2 config | `pm2 save` |
| Systemd logs | `sudo journalctl -u 1ai-trade-bot -f` |
| Systemd restart | `sudo systemctl restart 1ai-trade-bot` |

## Support

For issues, check:
1. PM2 logs: `pm2 logs 1ai-trade-bot`
2. systemd logs: `sudo journalctl -u 1ai-trade-bot -f`
3. Listening port: `lsof -i :8888`
4. Environment: `cat .env`
5. Dependencies: `pip list | grep -E 'yfinance|httpx|pydantic'`
