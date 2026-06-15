"""
Stockbit Broker — Indonesian social trading platform.

Stockbit uses REST API with token authentication.
Supports stocks, bonds, mutual funds with social features.
"""

from .broker import StockbitBroker

__all__ = ["StockbitBroker"]

