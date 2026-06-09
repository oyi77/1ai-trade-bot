# syntax=docker/dockerfile:1
# Multi-stage Docker build for TradeBot

# ── Stage 1: Builder ─────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libc6-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy only dependency files first for layer caching
COPY pyproject.toml README.md ./

# Create virtualenv and install production deps
RUN python -m venv /venv && \
    /venv/bin/pip install --no-cache-dir --upgrade pip setuptools wheel && \
    /venv/bin/pip install --no-cache-dir ".[prod]"

# Copy the actual package
COPY tradebot/ /build/tradebot/

# Install the package itself into the venv
RUN /venv/bin/pip install --no-cache-dir -e ".[prod]"

# ── Stage 2: Runtime ─────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

WORKDIR /app

# Install runtime system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    tini \
    && rm -rf /var/lib/apt/lists/*

# Copy venv from builder
COPY --from=builder /venv /venv

# Copy application code (for editable install reference)
COPY --from=builder /build/tradebot/ /app/tradebot/
COPY pyproject.toml /app/
COPY tradebot/config/.env.example /app/.env.example

# Set PATH to use the venv
ENV PATH="/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH="/app"

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -sf http://localhost:8082/health || exit 1

# Use tini as init for proper signal handling
ENTRYPOINT ["/usr/bin/tini", "--"]

# Default command (overridable)
CMD ["tradebot", "--help"]
