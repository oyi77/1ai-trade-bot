"""
Robinhood Broker — US commission-free trading platform.

Robinhood uses unofficial REST API with device token authentication.
Supports stocks, options, crypto, and fractional shares.

Note: Uses unofficial API - use at own risk. Official API not public.
"""

from .broker import RobinhoodBroker

__all__ = ["RobinhoodBroker"]

