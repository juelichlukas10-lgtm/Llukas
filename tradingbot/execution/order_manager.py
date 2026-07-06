"""Order-Verwaltung: Platzierung, Überwachung, Stornierung, Retry.

Der :class:`OrderManager` ist die einzige Stelle, die Orders an den
Exchange-Adapter übergibt. Er bietet:

    * Automatische Wiederholung bei Netzwerk-/Rate-Limit-Fehlern
    * Überwachung offener Orders (Status-Synchronisation)
    * Timeout-Stornierung nicht gefüllter Limit-Orders
    * Persistenz aller Order-Zustände (optional)
"""

from __future__ import annotations

from datetime import timedelta
from typing import Callable

from tradingbot.core.enums import OrderSide, OrderType
from tradingbot.core.exceptions import OrderError
from tradingbot.core.logging import get_logger
from tradingbot.core.models import Order, utc_now
from tradingbot.exchange.base import ExchangeAdapter, retry_async

logger = get_logger(__name__)


class OrderManager:
    """Verwaltet den Lebenszyklus aller Orders.

    Args:
        exchange: Verbundener Exchange-Adapter.
        on_order_update: Optionaler Callback (z. B. Datenbank-Persistenz),
            der bei jeder Statusänderung aufgerufen wird.
        limit_order_timeout_seconds: Nach dieser Zeit werden offene
            Limit-Orders storniert (0 = nie).
        max_retries: Wiederholungen bei transienten API-Fehlern.
    """

    def __init__(
        self,
        exchange: ExchangeAdapter,
        on_order_update: Callable[[Order], None] | None = None,
        limit_order_timeout_seconds: float = 300.0,
        max_retries: int = 3,
    ) -> None:
        self._exchange = exchange
        self._on_update = on_order_update
        self._timeout = limit_order_timeout_seconds
        self._max_retries = max_retries
        self._open_orders: dict[str, Order] = {}

    # ------------------------------------------------------------------
    # Platzierung
    # ------------------------------------------------------------------

    async def submit(self, order: Order) -> Order:
        """Platziert eine Order mit automatischem Retry.

        Args:
            order: Vorbereitete Order.

        Returns:
            Die aktualisierte Order (mit Exchange-ID und Status).

        Raises:
            OrderError: Wenn die Platzierung endgültig fehlschlägt.
        """
        order.amount = self._exchange.amount_to_precision(order.symbol, order.amount)
        if order.price is not None:
            order.price = self._exchange.price_to_precision(order.symbol, order.price)
        if order.amount <= 0:
            raise OrderError(f"Ordermenge {order.amount} ist nach Rundung nicht positiv")

        result = await retry_async(
            lambda: self._exchange.create_order(order),
            retries=self._max_retries,
            description=f"create_order {order.side.value} {order.symbol}",
        )
        if result.is_open:
            self._open_orders[result.id] = result
        self._notify(result)
        return result

    async def submit_market(
        self,
        symbol: str,
        side: OrderSide,
        amount: float,
        strategy: str = "",
        reduce_only: bool = False,
    ) -> Order:
        """Komfort-Methode: Market-Order platzieren."""
        order = Order(
            symbol=symbol,
            side=side,
            type=OrderType.MARKET,
            amount=amount,
            strategy=strategy,
            reduce_only=reduce_only,
        )
        return await self.submit(order)

    # ------------------------------------------------------------------
    # Überwachung
    # ------------------------------------------------------------------

    async def sync_open_orders(self) -> list[Order]:
        """Synchronisiert alle offenen Orders mit der Börse.

        Storniert Limit-Orders, deren Timeout überschritten ist.

        Returns:
            Liste der Orders, die seit dem letzten Sync gefüllt wurden.
        """
        newly_filled: list[Order] = []
        for order in list(self._open_orders.values()):
            try:
                refreshed = await retry_async(
                    lambda o=order: self._exchange.fetch_order(o),
                    retries=self._max_retries,
                    description=f"fetch_order {order.id[:8]}",
                )
            except Exception:
                logger.exception("Order-Sync für %s fehlgeschlagen", order.id[:8])
                continue

            if refreshed.status.is_terminal:
                self._open_orders.pop(order.id, None)
                self._notify(refreshed)
                if refreshed.filled > 0:
                    newly_filled.append(refreshed)
                continue

            if self._is_timed_out(refreshed):
                logger.warning(
                    "Limit-Order %s seit %ss offen – wird storniert",
                    refreshed.id[:8],
                    self._timeout,
                )
                await self.cancel(refreshed)
        return newly_filled

    def _is_timed_out(self, order: Order) -> bool:
        """True, wenn eine offene Limit-Order ihr Timeout überschritten hat."""
        if self._timeout <= 0 or order.type is not OrderType.LIMIT:
            return False
        return utc_now() - order.created_at > timedelta(seconds=self._timeout)

    # ------------------------------------------------------------------
    # Stornierung
    # ------------------------------------------------------------------

    async def cancel(self, order: Order) -> Order:
        """Storniert eine Order mit Retry."""
        result = await retry_async(
            lambda: self._exchange.cancel_order(order),
            retries=self._max_retries,
            description=f"cancel_order {order.id[:8]}",
        )
        self._open_orders.pop(order.id, None)
        self._notify(result)
        return result

    async def cancel_all(self, symbol: str | None = None) -> int:
        """Storniert alle (optional symbolgefilterten) offenen Orders.

        Returns:
            Anzahl der stornierten Orders.
        """
        count = 0
        for order in list(self._open_orders.values()):
            if symbol is not None and order.symbol != symbol:
                continue
            try:
                await self.cancel(order)
                count += 1
            except Exception:
                logger.exception("Stornierung von Order %s fehlgeschlagen", order.id[:8])
        return count

    # ------------------------------------------------------------------
    # Zugriff
    # ------------------------------------------------------------------

    @property
    def open_orders(self) -> list[Order]:
        """Kopie der aktuell verwalteten offenen Orders."""
        return list(self._open_orders.values())

    def track(self, order: Order) -> None:
        """Nimmt eine extern erstellte offene Order in die Überwachung auf."""
        if order.is_open:
            self._open_orders[order.id] = order

    def _notify(self, order: Order) -> None:
        """Ruft den Update-Callback auf (Fehler werden nur geloggt)."""
        if self._on_update is None:
            return
        try:
            self._on_update(order)
        except Exception:
            logger.exception("Order-Update-Callback fehlgeschlagen für %s", order.id[:8])
