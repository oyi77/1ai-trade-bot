#!/usr/bin/env bash
# Run the subscription bot (development)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

export PYTHONPATH="${PROJECT_DIR}:${PYTHONPATH:-}"

exec "${PROJECT_DIR}/.venv/bin/python3" "${SCRIPT_DIR}/bot.py" "$@"
