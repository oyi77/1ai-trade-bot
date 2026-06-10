# ─────────────────────────────────────────────────────────────
# Vilona Autonomous Production Worker — Railway Docker Image
# Multi-stage: builder → slim runtime
# ─────────────────────────────────────────────────────────────

FROM python:3.11-slim-bookworm AS builder

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# ── Runtime ──
FROM python:3.11-slim-bookworm

# System deps for yfinance data, timezone, and health checks
RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ENV TZ=Asia/Jakarta

WORKDIR /app

# Copy installed packages
COPY --from=builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH

# Copy application code
COPY . .

# ── Runtime config ──
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
ENV PHANTOMFX_WEBHOOK_URL=https://phantomfx.aitradepulse.com/id

# Railway provides PORT automatically
EXPOSE ${PORT:-8080}

# Health check — uses heartbeat file
HEALTHCHECK --interval=60s --timeout=10s --retries=3 \
    CMD python3 -c "import json,time; from pathlib import Path; \
    hb=Path('data/vilona_tradefx/heartbeat.json'); \
    exit(0 if hb.exists() and time.time()-json.loads(hb.read_text()).get('unix',0)<120 else 1)"

# Entry: 4-Pillar Autonomous Worker
CMD ["python3", "-m", "tradebot.engines.vilona_worker"]
