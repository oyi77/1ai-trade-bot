#!/usr/bin/env python3
"""
⚠️  DEPRECATED — use ``tradebot`` CLI instead.

This file kept for backward compatibility.
It now delegates to ``tradebot.cli:main``.

Migration:
    python -m scripts.deriv test       →  tradebot test
    python -m scripts.deriv trade R_75 →  tradebot trade R_75
    python -m scripts.deriv stream     →  tradebot stream
"""

import sys
import warnings

warnings.warn(
    "scripts/deriv/ is deprecated — use 'tradebot' CLI instead. "
    "Run: tradebot --help",
    DeprecationWarning,
    stacklevel=2,
)

# Delegate to the unified CLI
from tradebot.cli import main

if __name__ == "__main__":
    # Map old positional modes to new subcommands
    # Old: python -m scripts.deriv test
    # New: tradebot test
    # Old: python -m scripts.deriv debug R_75
    # New: tradebot test R_75
    mapping = {
        "debug": "test",      # old 'debug' → new 'test'
    }

    argv = list(sys.argv[1:])
    if argv and argv[0] in mapping:
        argv[0] = mapping[argv[0]]

    sys.exit(main(argv))
