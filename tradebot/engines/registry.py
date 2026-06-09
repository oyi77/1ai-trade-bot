"""
Engine registry — auto-discovers and registers signal analysis engines.
"""
import importlib
import inspect
import logging
import pkgutil

from tradebot.engines.base import Engine

LOG = logging.getLogger(__name__)


class Registry:
    """Auto-discovers and registers signal analysis engines.

    Scans the tradebot.engines module (and submodules) for Engine subclasses.
    Also supports explicit registration.
    """

    def __init__(self):
        self._engines: dict[str, Engine] = {}

    def discover(self, package: str = "tradebot.engines") -> dict[str, Engine]:
        """Discover all Engine subclasses in the given package."""
        try:
            pkg = importlib.import_module(package)
        except ImportError:
            LOG.warning("Package %s not found", package)
            return {}

        for _, name, is_pkg in pkgutil.walk_packages(
            pkg.__path__, pkg.__name__ + "."
        ):
            if is_pkg:
                continue
            try:
                mod = importlib.import_module(name)
            except Exception as e:
                LOG.debug("Skipping module %s: %s", name, e)
                continue

            for _, obj in inspect.getmembers(mod, inspect.isclass):
                if (
                    issubclass(obj, Engine)
                    and obj is not Engine
                    and not inspect.isabstract(obj)
                ):
                    try:
                        instance = obj()
                        self._engines[instance.name] = instance
                        LOG.info("Discovered engine: %s (%s)", instance.name, name)
                    except Exception as e:
                        LOG.debug("Could not instantiate %s: %s", obj.__name__, e)

        return self._engines

    def register(self, engine: Engine):
        """Manually register an engine instance."""
        self._engines[engine.name] = engine

    def get(self, name: str) -> Engine | None:
        return self._engines.get(name)

    @property
    def all(self) -> dict[str, Engine]:
        return dict(self._engines)
