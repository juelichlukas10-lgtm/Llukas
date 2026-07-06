"""Strategie-Registry mit automatischem Plugin-Loading.

Jede Datei im Paket ``tradingbot.strategies`` (außer ``base``/``registry``)
gilt als Plugin: Beim Aufruf von :func:`discover_strategies` werden alle
Module importiert, wodurch sich die enthaltenen Strategien über den
``@register_strategy``-Decorator selbst registrieren. Neue Strategien
erfordern damit keinerlei Änderung am Kernsystem.
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import Any, Type

from tradingbot.core.enums import Timeframe
from tradingbot.core.exceptions import StrategyError
from tradingbot.core.logging import get_logger
from tradingbot.strategies.base import Strategy

logger = get_logger(__name__)

_REGISTRY: dict[str, Type[Strategy]] = {}
_discovered = False

#: Module, die keine Plugins sind.
_NON_PLUGIN_MODULES = {"base", "registry", "__init__"}


def register_strategy(name: str) -> Any:
    """Decorator: registriert eine Strategie-Klasse unter ``name``.

    Beispiel:
        >>> @register_strategy("my_strategy")
        ... class MyStrategy(Strategy):
        ...     ...

    Args:
        name: Eindeutiger Registry-Name (wird auch als ``cls.name`` gesetzt).

    Raises:
        StrategyError: Wenn der Name bereits durch eine andere Klasse belegt ist.
    """

    def decorator(cls: Type[Strategy]) -> Type[Strategy]:
        if not issubclass(cls, Strategy):
            raise StrategyError(f"{cls.__name__} muss von Strategy erben")
        key = name.lower()
        existing = _REGISTRY.get(key)
        if existing is not None and existing is not cls:
            raise StrategyError(f"Strategie-Name '{key}' ist bereits registriert ({existing.__name__})")
        cls.name = key
        _REGISTRY[key] = cls
        return cls

    return decorator


def discover_strategies() -> dict[str, Type[Strategy]]:
    """Importiert alle Plugin-Module und liefert die Registry.

    Fehlerhafte Plugins werden geloggt und übersprungen, damit ein
    defektes Plugin nicht den gesamten Bot verhindert.

    Returns:
        Mapping ``name -> Strategieklasse``.
    """
    global _discovered
    if _discovered:
        return dict(_REGISTRY)

    import tradingbot.strategies as package

    for module_info in pkgutil.iter_modules(package.__path__):
        if module_info.name in _NON_PLUGIN_MODULES or module_info.name.startswith("_"):
            continue
        module_name = f"{package.__name__}.{module_info.name}"
        try:
            importlib.import_module(module_name)
        except Exception:
            logger.exception("Strategie-Plugin '%s' konnte nicht geladen werden", module_name)

    _discovered = True
    logger.info("Strategie-Registry: %d Strategien geladen: %s",
                len(_REGISTRY), ", ".join(sorted(_REGISTRY)))
    return dict(_REGISTRY)


def get_strategy_class(name: str) -> Type[Strategy]:
    """Liefert die Strategieklasse zu einem Namen.

    Raises:
        StrategyError: Wenn der Name unbekannt ist.
    """
    discover_strategies()
    key = name.lower()
    if key not in _REGISTRY:
        available = ", ".join(sorted(_REGISTRY)) or "(keine)"
        raise StrategyError(f"Unbekannte Strategie '{name}'. Verfügbar: {available}")
    return _REGISTRY[key]


def create_strategy(
    name: str,
    symbols: list[str],
    timeframe: Timeframe = Timeframe.M5,
    params: dict[str, Any] | None = None,
) -> Strategy:
    """Instanziiert eine registrierte Strategie.

    Args:
        name: Registry-Name der Strategie.
        symbols: Symbole, auf denen die Strategie arbeiten soll.
        timeframe: Haupt-Timeframe.
        params: Parameter-Overrides.

    Returns:
        Konfigurierte Strategie-Instanz.
    """
    cls = get_strategy_class(name)
    return cls(symbols=symbols, timeframe=timeframe, params=params)


def list_strategies() -> list[str]:
    """Alphabetische Liste aller registrierten Strategie-Namen."""
    discover_strategies()
    return sorted(_REGISTRY)
