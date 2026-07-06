"""Asynchroner Event-Bus zur losen Kopplung der Module.

Module publizieren Ereignisse (z. B. neue Kerze, Order gefüllt, Trade
geschlossen), ohne ihre Konsumenten zu kennen. Handler können synchron
oder asynchron sein; Fehler in einem Handler beeinträchtigen andere
Handler nicht.
"""

from __future__ import annotations

import asyncio
import inspect
from collections import defaultdict
from enum import StrEnum
from typing import Any, Awaitable, Callable

from tradingbot.core.logging import get_logger

logger = get_logger(__name__)

EventHandler = Callable[[Any], None] | Callable[[Any], Awaitable[None]]


class EventType(StrEnum):
    """Systemweite Ereignistypen."""

    CANDLE = "candle"
    TICKER = "ticker"
    ORDER_BOOK = "order_book"
    TRADE_TICK = "trade_tick"
    SIGNAL = "signal"
    ORDER_PLACED = "order_placed"
    ORDER_FILLED = "order_filled"
    ORDER_CANCELED = "order_canceled"
    POSITION_OPENED = "position_opened"
    POSITION_CLOSED = "position_closed"
    TRADE_CLOSED = "trade_closed"
    RISK_LIMIT_HIT = "risk_limit_hit"
    ERROR = "error"


class EventBus:
    """Einfacher asynchroner Publish/Subscribe-Bus.

    Beispiel:
        >>> bus = EventBus()
        >>> bus.subscribe(EventType.SIGNAL, my_handler)
        >>> await bus.publish(EventType.SIGNAL, signal)
    """

    def __init__(self) -> None:
        self._handlers: dict[EventType, list[EventHandler]] = defaultdict(list)

    def subscribe(self, event_type: EventType, handler: EventHandler) -> None:
        """Registriert einen Handler für einen Ereignistyp.

        Args:
            event_type: Zu abonnierender Ereignistyp.
            handler: Synchrone oder asynchrone Callback-Funktion.
        """
        self._handlers[event_type].append(handler)

    def unsubscribe(self, event_type: EventType, handler: EventHandler) -> None:
        """Entfernt einen zuvor registrierten Handler (no-op falls unbekannt)."""
        try:
            self._handlers[event_type].remove(handler)
        except ValueError:
            pass

    async def publish(self, event_type: EventType, payload: Any = None) -> None:
        """Publiziert ein Ereignis an alle Handler.

        Asynchrone Handler werden awaited, synchrone direkt aufgerufen.
        Exceptions einzelner Handler werden geloggt und unterdrückt, damit
        ein fehlerhafter Konsument den Bus nicht blockiert.

        Args:
            event_type: Ereignistyp.
            payload: Beliebige Nutzdaten des Ereignisses.
        """
        for handler in list(self._handlers.get(event_type, ())):
            try:
                result = handler(payload)
                if inspect.isawaitable(result):
                    await result
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Event-Handler %r für '%s' hat eine Exception ausgelöst",
                    getattr(handler, "__qualname__", handler),
                    event_type,
                )

    def clear(self) -> None:
        """Entfernt alle Handler (primär für Tests)."""
        self._handlers.clear()
