"""Provider registry — service locator for market providers.

Providers register themselves here by name. Consumers retrieve providers
without importing concrete implementations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trading_bot.providers.base import BaseProvider


class ProviderRegistry:
    """Service locator for market providers.

    Usage::

        registry = ProviderRegistry()
        registry.register(paper_provider)
        provider = registry.get("paper")
    """

    def __init__(self) -> None:
        self._providers: dict[str, BaseProvider] = {}

    def register(self, provider: BaseProvider) -> None:
        """Register a provider instance."""
        self._providers[provider.name] = provider

    def unregister(self, name: str) -> None:
        """Remove a provider by name."""
        self._providers.pop(name, None)

    def get(self, name: str) -> BaseProvider | None:
        """Get a provider by name, or None if not found."""
        return self._providers.get(name)

    def get_all(self) -> list[BaseProvider]:
        """Return all registered providers."""
        return list(self._providers.values())

    def list_by_type(self, market_type: str) -> list[BaseProvider]:
        """Return providers matching a market type."""
        return [
            p
            for p in self._providers.values()
            if p.market_type.value == market_type
        ]

    def clear(self) -> None:
        """Remove all registered providers."""
        self._providers.clear()

    @property
    def count(self) -> int:
        """Number of registered providers."""
        return len(self._providers)
