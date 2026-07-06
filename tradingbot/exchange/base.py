"""Abstrakte Exchange-Schnittstelle.

Alle Börsen-Adapter (CCXT-basiert, Paper-Simulator, Test-Mocks)
implementieren dieses Interface. Die übrigen Module des Bots kennen
ausschließlich diese Abstraktion – neue Börsen lassen sich damit ohne
Änderungen am Kernsystem ergänzen.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Awaitable, Callable, TypeVar

from tradingbot.core.enums import Timeframe
from tradingbot.core.exceptions import ExchangeConnectionError, RateLimitError
from tradingbot.core.logging import get_logger
from tradingbot.core.models import (
    Balance,
    Candle,
    FundingRate,
    OpenInterest,
    Order,
    OrderBook,
    Ticker,
    TradeTick,
)

logger = get_logger(__name__)

T = TypeVar("T")


async def retry_async(
    func: Callable[[], Awaitable[T]],
    *,
    retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    retry_on: tuple[type[BaseException], ...] = (ExchangeConnectionError, RateLimitError),
    description: str = "exchange call",
) -> T:
    """Führt eine asynchrone Operation mit exponentiellem Backoff erneut aus.

    Args:
        func: Parameterlose Coroutine-Factory (z. B. ``lambda: adapter.fetch_ticker(s)``).
        retries: Maximale Anzahl von Wiederholungen nach dem Erstversuch.
        base_delay: Wartezeit vor der ersten Wiederholung in Sekunden.
        max_delay: Obergrenze der Wartezeit.
        retry_on: Exception-Typen, die eine Wiederholung auslösen.
        description: Beschreibung für Log-Meldungen.

    Returns:
        Ergebnis von ``func``.

    Raises:
        Die letzte Exception, wenn alle Versuche fehlschlagen.
    """
    attempt = 0
    while True:
        try:
            return await func()
        except retry_on as exc:
            attempt += 1
            if attempt > retries:
                logger.error("%s endgültig fehlgeschlagen nach %d Versuchen: %s", description, attempt, exc)
                raise
            delay = min(base_delay * 2 ** (attempt - 1), max_delay)
            logger.warning(
                "%s fehlgeschlagen (Versuch %d/%d): %s – neuer Versuch in %.1fs",
                description,
                attempt,
                retries,
                exc,
                delay,
            )
            await asyncio.sleep(delay)


class ExchangeAdapter(ABC):
    """Einheitliche, asynchrone Schnittstelle zu einer Börse.

    REST-Methoden (``fetch_*``, ``create_order`` usw.) liefern einmalige
    Snapshots; ``watch_*``-Methoden sind asynchrone Generatoren für
    Live-Streams (WebSocket) inklusive automatischer Wiederverbindung.
    """

    #: Registry-Name der Börse (z. B. "binance").
    name: str = "abstract"

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @abstractmethod
    async def connect(self) -> None:
        """Initialisiert Verbindungen und lädt Marktmetadaten."""

    @abstractmethod
    async def close(self) -> None:
        """Schließt alle offenen Verbindungen (REST-Sessions, WebSockets)."""

    # ------------------------------------------------------------------
    # Marktdaten (REST)
    # ------------------------------------------------------------------

    @abstractmethod
    async def fetch_ticker(self, symbol: str) -> Ticker:
        """Aktuellen Ticker eines Symbols abrufen."""

    @abstractmethod
    async def fetch_order_book(self, symbol: str, limit: int = 25) -> OrderBook:
        """Orderbuch-Snapshot abrufen.

        Args:
            symbol: Handelspaar.
            limit: Anzahl der Levels je Seite.
        """

    @abstractmethod
    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: Timeframe,
        since: int | None = None,
        limit: int = 500,
    ) -> list[Candle]:
        """Historische Kerzen abrufen.

        Args:
            symbol: Handelspaar.
            timeframe: Kerzen-Timeframe.
            since: Startzeitpunkt als Unix-Millisekunden (None = jüngste Kerzen).
            limit: Maximale Anzahl Kerzen.
        """

    @abstractmethod
    async def fetch_trades(self, symbol: str, limit: int = 100) -> list[TradeTick]:
        """Jüngste öffentliche Trades abrufen."""

    @abstractmethod
    async def fetch_funding_rate(self, symbol: str) -> FundingRate | None:
        """Aktuelle Funding-Rate (nur Futures; None wenn nicht verfügbar)."""

    @abstractmethod
    async def fetch_open_interest(self, symbol: str) -> OpenInterest | None:
        """Aktuelles Open Interest (nur Futures; None wenn nicht verfügbar)."""

    # ------------------------------------------------------------------
    # Konto & Orders (REST)
    # ------------------------------------------------------------------

    @abstractmethod
    async def fetch_balance(self) -> dict[str, Balance]:
        """Kontoguthaben je Währung abrufen."""

    @abstractmethod
    async def create_order(self, order: Order) -> Order:
        """Platziert eine Order an der Börse.

        Args:
            order: Zu platzierende Order (interne Repräsentation).

        Returns:
            Aktualisierte Order mit ``exchange_id`` und Status.
        """

    @abstractmethod
    async def cancel_order(self, order: Order) -> Order:
        """Storniert eine offene Order."""

    @abstractmethod
    async def fetch_order(self, order: Order) -> Order:
        """Aktualisiert Status/Fills einer Order von der Börse."""

    @abstractmethod
    async def fetch_open_orders(self, symbol: str | None = None) -> list[Order]:
        """Alle offenen Orders (optional auf ein Symbol gefiltert)."""

    @abstractmethod
    async def set_leverage(self, symbol: str, leverage: float) -> None:
        """Setzt den Hebel für ein Symbol (no-op auf Spot-Märkten)."""

    # ------------------------------------------------------------------
    # Live-Streams (WebSocket)
    # ------------------------------------------------------------------

    @abstractmethod
    def watch_ticker(self, symbol: str) -> AsyncIterator[Ticker]:
        """Asynchroner Ticker-Stream mit automatischem Reconnect."""

    @abstractmethod
    def watch_candles(self, symbol: str, timeframe: Timeframe) -> AsyncIterator[Candle]:
        """Asynchroner Stream abgeschlossener/aktualisierter Kerzen."""

    @abstractmethod
    def watch_order_book(self, symbol: str, limit: int = 25) -> AsyncIterator[OrderBook]:
        """Asynchroner Orderbuch-Stream."""

    @abstractmethod
    def watch_trades(self, symbol: str) -> AsyncIterator[TradeTick]:
        """Asynchroner Stream öffentlicher Trades."""

    # ------------------------------------------------------------------
    # Markt-Metadaten
    # ------------------------------------------------------------------

    @abstractmethod
    def market_info(self, symbol: str) -> dict[str, Any]:
        """Metadaten eines Marktes (Präzision, Mindestgrößen, Limits)."""

    def amount_to_precision(self, symbol: str, amount: float) -> float:
        """Rundet eine Menge auf die Präzision des Marktes (Default: 8 Stellen)."""
        return round(amount, 8)

    def price_to_precision(self, symbol: str, price: float) -> float:
        """Rundet einen Preis auf die Präzision des Marktes (Default: 8 Stellen)."""
        return round(price, 8)
