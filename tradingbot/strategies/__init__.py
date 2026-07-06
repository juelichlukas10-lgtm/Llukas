"""Strategie-Plugins.

Jede Datei in diesem Paket (außer ``base`` und ``registry``) ist ein
eigenständiges Strategie-Plugin. Neue Strategien werden durch Ablegen
einer einzelnen Python-Datei mit einer ``@register_strategy``-dekorierten
Klasse hinzugefügt – ohne Änderungen am Kernsystem.
"""

from tradingbot.strategies.base import Strategy, StrategyContext
from tradingbot.strategies.registry import (
    create_strategy,
    discover_strategies,
    get_strategy_class,
    list_strategies,
    register_strategy,
)

__all__ = [
    "Strategy",
    "StrategyContext",
    "create_strategy",
    "discover_strategies",
    "get_strategy_class",
    "list_strategies",
    "register_strategy",
]
