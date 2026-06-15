"""Provider registry — service locator for market providers.

Providers register themselves here by name. Consumers retrieve providers
without importing concrete implementations. Built-in providers are registered
lazily as factories to avoid heavy imports at registry construction time.
"""

from __future__ import annotations

from collections.abc import Callable
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
        self._factories: dict[str, Callable[[], BaseProvider]] = {}
        self._register_builtin_factories()

    def _register_builtin_factories(self) -> None:
        """Register lazy factories for built-in provider classes."""
        self.register_factory("paper", lambda: self._import_paper())
        self.register_factory("ccxt", lambda: self._import_ccxt())
        self.register_factory("exness", lambda: self._import_exness())

    @staticmethod
    def _import_paper() -> BaseProvider:
        from trading_bot.providers.paper.paper_trader import PaperTradingProvider

        return PaperTradingProvider()

    @staticmethod
    def _import_ccxt() -> BaseProvider:
        from trading_bot.providers.crypto.ccxt_adapter import CCXTProvider

        return CCXTProvider()

    @staticmethod
    def _import_exness() -> BaseProvider:
        from trading_bot.providers.forex.exness import ExnessProvider

        return ExnessProvider()

    def register_factory(self, name: str, factory: Callable[[], BaseProvider]) -> None:
        """Register a factory that creates a provider on first access."""
        self._factories[name] = factory

    def register_class(self, name: str, cls: type[BaseProvider]) -> None:
        """Register a provider class lazily by name."""
        self.register_factory(name, lambda: cls())

    def register(self, provider: BaseProvider) -> None:
        """Register a provider instance."""
        self._providers[provider.name] = provider

    def unregister(self, name: str) -> None:
        """Remove a provider by name."""
        self._providers.pop(name, None)
        self._factories.pop(name, None)

    def get(self, name: str) -> BaseProvider | None:
        """Get a provider by name, or None if not found.

        Lazily instantiates providers registered via factory or class.
        """
        if name in self._providers:
            return self._providers[name]
        factory = self._factories.get(name)
        if factory is None:
            return None
        provider = factory()
        self._providers[name] = provider
        return provider

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
        self._factories.clear()

    @property
    def count(self) -> int:
        """Number of registered provider instances."""
        return len(self._providers)

