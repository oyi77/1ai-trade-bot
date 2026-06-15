"""

Data providers for market feeds, orders, and normalized trade streams.

Each provider exposes a consistent async interface consumed by the
unified bot's signal pipeline and Telegram adapters.

LAYOUT (flask-only directory; no subpackages):
  - sample_providers.py   → static sample data for testing
  - normalize_trades.py   → raw trade normalization
  - sample_orders_feed.py → synthetic order stream
"""

from . import sample_providers, normalize_trades, sample_orders_feed  # noqa: F401
