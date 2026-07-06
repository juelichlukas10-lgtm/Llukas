"""Paper-Trading-Exchange: realistische Order-Simulation ohne echtes Geld.

Der :class:`PaperExchangeAdapter` implementiert die vollständige
:class:`~tradingbot.exchange.base.ExchangeAdapter`-Schnittstelle.
Marktdaten werden von einem optionalen inneren (öffentlichen) Adapter
bezogen; die Kontoführung und Orderausführung werden lokal simuliert:

    * Kommission, Slippage und Spread konfigurierbar
    * Market-, Limit-, Stop-Market-, Stop-Limit- und Trailing-Stop-Orders
    * Teilausführungen werden über ``process_tick`` preisgetrieben ausgelöst
    * Guthabenprüfung mit :class:`InsufficientFundsError`

Ohne inneren Adapter (z. B. in Tests) werden Preise über
:meth:`set_price` eingespeist.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, AsyncIterator

from tradingbot.core.config import PaperConfig
from tradingbot.core.enums import OrderSide, OrderStatus, OrderType, Timeframe
from tradingbot.core.exceptions import (
    DataError,
    InsufficientFundsError,
    OrderError,
)
from tradingbot.core.logging import get_logger
from tradingbot.core.models import (
    Balance,
    Candle,
    FundingRate,
    OpenInterest,
    Order,
    OrderBook,
    OrderBookLevel,
    Ticker,
    TradeTick,
)
from tradingbot.exchange.base import ExchangeAdapter

logger = get_logger(__name__)


def _split_symbol(symbol: str) -> tuple[str, str]:
    """Zerlegt ein Handelspaar in Basis- und Quote-Währung."""
    if "/" not in symbol:
        raise OrderError(f"Ungültiges Symbol '{symbol}' – erwartet Format 'BASE/QUOTE'")
    base, _, quote = symbol.partition("/")
    # Futures-Symbole wie "BTC/USDT:USDT" normalisieren.
    quote = quote.split(":")[0]
    return base, quote


class PaperExchangeAdapter(ExchangeAdapter):
    """Simulierte Börse für Paper-Trading.

    Args:
        config: Simulationsparameter (Startkapital, Kommission, Slippage, Spread).
        data_provider: Optionaler realer Adapter für Live-Marktdaten.
            None = Preise werden über :meth:`set_price` eingespeist (Tests).
        quote_currency: Währung des Startkapitals.
    """

    name = "paper"

    def __init__(
        self,
        config: PaperConfig | None = None,
        data_provider: ExchangeAdapter | None = None,
        quote_currency: str = "USDT",
    ) -> None:
        self._config = config or PaperConfig()
        self._data_provider = data_provider
        self._quote = quote_currency
        self._balances: dict[str, float] = {quote_currency: self._config.initial_balance}
        self._reserved: dict[str, float] = {}
        self._open_orders: dict[str, Order] = {}
        self._last_price: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Verbindet den optionalen Daten-Provider."""
        if self._data_provider is not None:
            await self._data_provider.connect()
        logger.info(
            "Paper-Exchange gestartet: %.2f %s Startkapital, %.4f%% Kommission",
            self._config.initial_balance,
            self._quote,
            self._config.commission_rate * 100,
        )

    async def close(self) -> None:
        """Schließt den optionalen Daten-Provider."""
        if self._data_provider is not None:
            await self._data_provider.close()

    # ------------------------------------------------------------------
    # Preis-Einspeisung & Simulation
    # ------------------------------------------------------------------

    def set_price(self, symbol: str, price: float) -> None:
        """Setzt den aktuellen Simulationspreis (primär für Tests).

        Löst wie :meth:`process_tick` die Prüfung offener Orders aus.
        """
        self.process_tick(symbol, price)

    def process_tick(self, symbol: str, price: float) -> list[Order]:
        """Verarbeitet ein Preisupdate und führt fällige Orders aus.

        Args:
            symbol: Handelspaar des Updates.
            price: Neuer Marktpreis.

        Returns:
            Liste der durch dieses Update (teil-)ausgeführten Orders.
        """
        if price <= 0:
            raise DataError(f"Ungültiger Preis {price} für {symbol}")
        self._last_price[symbol] = price
        filled: list[Order] = []
        for order in list(self._open_orders.values()):
            if order.symbol != symbol or not order.is_open:
                continue
            if self._try_fill_pending(order, price):
                filled.append(order)
            if order.status.is_terminal:
                self._open_orders.pop(order.id, None)
        return filled

    def last_price(self, symbol: str) -> float:
        """Letzter bekannter Preis eines Symbols.

        Raises:
            DataError: Wenn noch kein Preis vorliegt.
        """
        if symbol not in self._last_price:
            raise DataError(f"Kein Preis für {symbol} verfügbar – set_price()/Ticker nötig")
        return self._last_price[symbol]

    # ------------------------------------------------------------------
    # Marktdaten – Delegation an den Daten-Provider
    # ------------------------------------------------------------------

    async def fetch_ticker(self, symbol: str) -> Ticker:
        if self._data_provider is not None:
            ticker = await self._data_provider.fetch_ticker(symbol)
            self._last_price[symbol] = ticker.last
            self.process_tick(symbol, ticker.last)
            return ticker
        price = self.last_price(symbol)
        half_spread = price * self._config.spread_rate / 2.0
        return Ticker(
            symbol=symbol,
            timestamp=datetime.now(timezone.utc),
            bid=price - half_spread,
            ask=price + half_spread,
            last=price,
        )

    async def fetch_order_book(self, symbol: str, limit: int = 25) -> OrderBook:
        if self._data_provider is not None:
            return await self._data_provider.fetch_order_book(symbol, limit)
        price = self.last_price(symbol)
        half_spread = max(price * self._config.spread_rate / 2.0, 1e-9)
        bids = tuple(
            OrderBookLevel(price=price - half_spread * (i + 1), amount=1.0) for i in range(limit)
        )
        asks = tuple(
            OrderBookLevel(price=price + half_spread * (i + 1), amount=1.0) for i in range(limit)
        )
        return OrderBook(symbol=symbol, timestamp=datetime.now(timezone.utc), bids=bids, asks=asks)

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: Timeframe,
        since: int | None = None,
        limit: int = 500,
    ) -> list[Candle]:
        if self._data_provider is None:
            raise DataError("Paper-Exchange ohne Daten-Provider kann keine Kerzen liefern")
        return await self._data_provider.fetch_ohlcv(symbol, timeframe, since=since, limit=limit)

    async def fetch_trades(self, symbol: str, limit: int = 100) -> list[TradeTick]:
        if self._data_provider is None:
            return []
        return await self._data_provider.fetch_trades(symbol, limit)

    async def fetch_funding_rate(self, symbol: str) -> FundingRate | None:
        if self._data_provider is None:
            return None
        return await self._data_provider.fetch_funding_rate(symbol)

    async def fetch_open_interest(self, symbol: str) -> OpenInterest | None:
        if self._data_provider is None:
            return None
        return await self._data_provider.fetch_open_interest(symbol)

    # ------------------------------------------------------------------
    # Konto & Orders (Simulation)
    # ------------------------------------------------------------------

    async def fetch_balance(self) -> dict[str, Balance]:
        result: dict[str, Balance] = {}
        for currency, total in self._balances.items():
            used = self._reserved.get(currency, 0.0)
            result[currency] = Balance(currency=currency, free=total - used, used=used)
        return result

    async def create_order(self, order: Order) -> Order:
        if order.amount <= 0:
            raise OrderError("Ordermenge muss positiv sein")
        order.exchange_id = f"paper-{order.id[:12]}"

        price = self._last_price.get(order.symbol)
        if price is None:
            if self._data_provider is not None:
                ticker = await self.fetch_ticker(order.symbol)
                price = ticker.last
            else:
                raise DataError(f"Kein Preis für {order.symbol} – Order nicht simulierbar")

        if order.type is OrderType.MARKET:
            self._execute_fill(order, self._market_fill_price(order.side, price))
        elif order.type is OrderType.TRAILING_STOP:
            if order.trailing_delta is None or order.trailing_delta <= 0:
                raise OrderError("Trailing-Stop-Order benötigt trailing_delta > 0")
            # Initialen Auslösepreis vom aktuellen Kurs ableiten.
            if order.side is OrderSide.SELL:
                order.stop_price = price * (1.0 - order.trailing_delta)
            else:
                order.stop_price = price * (1.0 + order.trailing_delta)
            self._register_pending(order)
        else:
            self._validate_pending(order)
            self._register_pending(order)
        return order

    async def cancel_order(self, order: Order) -> Order:
        pending = self._open_orders.pop(order.id, None)
        if pending is None:
            raise OrderError(f"Order {order.id} ist nicht offen")
        self._release_reservation(pending)
        pending.status = OrderStatus.CANCELED
        order.status = OrderStatus.CANCELED
        logger.info("Paper-Order storniert: %s %s", order.id, order.symbol)
        return order

    async def fetch_order(self, order: Order) -> Order:
        stored = self._open_orders.get(order.id)
        if stored is not None:
            return stored
        return order

    async def fetch_open_orders(self, symbol: str | None = None) -> list[Order]:
        orders = [o for o in self._open_orders.values() if o.is_open]
        if symbol is not None:
            orders = [o for o in orders if o.symbol == symbol]
        return orders

    async def set_leverage(self, symbol: str, leverage: float) -> None:
        logger.debug("Paper-Exchange: set_leverage(%s, %.1f) ignoriert", symbol, leverage)

    # ------------------------------------------------------------------
    # Streams – Delegation an den Daten-Provider
    # ------------------------------------------------------------------

    async def watch_ticker(self, symbol: str) -> AsyncIterator[Ticker]:
        if self._data_provider is None:
            raise DataError("Paper-Exchange ohne Daten-Provider hat keine Live-Streams")
        async for ticker in self._data_provider.watch_ticker(symbol):
            self.process_tick(symbol, ticker.last)
            yield ticker

    async def watch_candles(self, symbol: str, timeframe: Timeframe) -> AsyncIterator[Candle]:
        if self._data_provider is None:
            raise DataError("Paper-Exchange ohne Daten-Provider hat keine Live-Streams")
        async for candle in self._data_provider.watch_candles(symbol, timeframe):
            self.process_tick(symbol, candle.close)
            yield candle

    async def watch_order_book(self, symbol: str, limit: int = 25) -> AsyncIterator[OrderBook]:
        if self._data_provider is None:
            raise DataError("Paper-Exchange ohne Daten-Provider hat keine Live-Streams")
        async for book in self._data_provider.watch_order_book(symbol, limit):
            yield book

    async def watch_trades(self, symbol: str) -> AsyncIterator[TradeTick]:
        if self._data_provider is None:
            raise DataError("Paper-Exchange ohne Daten-Provider hat keine Live-Streams")
        async for trade in self._data_provider.watch_trades(symbol):
            yield trade

    # ------------------------------------------------------------------
    # Markt-Metadaten
    # ------------------------------------------------------------------

    def market_info(self, symbol: str) -> dict[str, Any]:
        if self._data_provider is not None:
            return self._data_provider.market_info(symbol)
        base, quote = _split_symbol(symbol)
        return {
            "symbol": symbol,
            "base": base,
            "quote": quote,
            "precision": {"amount": 8, "price": 8},
            "limits": {"amount": {"min": 0.0}, "cost": {"min": 0.0}},
        }

    # ------------------------------------------------------------------
    # Interne Simulationslogik
    # ------------------------------------------------------------------

    def _market_fill_price(self, side: OrderSide, price: float) -> float:
        """Ausführungspreis einer Market-Order inkl. Spread und Slippage."""
        adjustment = self._config.spread_rate / 2.0 + self._config.slippage_rate
        if side is OrderSide.BUY:
            return price * (1.0 + adjustment)
        return price * (1.0 - adjustment)

    def _validate_pending(self, order: Order) -> None:
        """Validiert Pflichtfelder von Limit-/Stop-Orders."""
        if order.type is OrderType.LIMIT and order.price is None:
            raise OrderError("Limit-Order benötigt einen Preis")
        if order.type is OrderType.STOP_MARKET and order.stop_price is None:
            raise OrderError("Stop-Market-Order benötigt stop_price")
        if order.type is OrderType.STOP_LIMIT and (order.stop_price is None or order.price is None):
            raise OrderError("Stop-Limit-Order benötigt stop_price und price")

    def _register_pending(self, order: Order) -> None:
        """Nimmt eine Order ins offene Orderbuch auf und reserviert Guthaben."""
        self._reserve_for_order(order)
        order.status = OrderStatus.OPEN
        self._open_orders[order.id] = order
        logger.info(
            "Paper-Order offen: %s %s %s %.8f (type=%s, price=%s, stop=%s)",
            order.id[:8],
            order.side.value,
            order.symbol,
            order.amount,
            order.type.value,
            order.price,
            order.stop_price,
        )

    def _try_fill_pending(self, order: Order, price: float) -> bool:
        """Prüft, ob eine offene Order beim gegebenen Preis ausgeführt wird."""
        match order.type:
            case OrderType.LIMIT:
                assert order.price is not None
                if (order.side is OrderSide.BUY and price <= order.price) or (
                    order.side is OrderSide.SELL and price >= order.price
                ):
                    # Limit-Orders füllen zum Limitpreis (Maker, keine Slippage).
                    self._execute_fill(order, order.price, release_first=True)
                    return True
            case OrderType.STOP_MARKET:
                assert order.stop_price is not None
                if self._stop_triggered(order, price):
                    self._execute_fill(
                        order, self._market_fill_price(order.side, price), release_first=True
                    )
                    return True
            case OrderType.STOP_LIMIT:
                assert order.stop_price is not None and order.price is not None
                if self._stop_triggered(order, price):
                    # Nach Auslösung wie eine Limit-Order behandeln.
                    order.type = OrderType.LIMIT
                    order.stop_price = None
                    if (order.side is OrderSide.BUY and price <= order.price) or (
                        order.side is OrderSide.SELL and price >= order.price
                    ):
                        self._execute_fill(order, order.price, release_first=True)
                        return True
            case OrderType.TRAILING_STOP:
                assert order.stop_price is not None and order.trailing_delta is not None
                if order.side is OrderSide.SELL:
                    # Stop nach oben nachziehen.
                    new_stop = price * (1.0 - order.trailing_delta)
                    if new_stop > order.stop_price:
                        order.stop_price = new_stop
                    elif price <= order.stop_price:
                        self._execute_fill(
                            order, self._market_fill_price(order.side, price), release_first=True
                        )
                        return True
                else:
                    new_stop = price * (1.0 + order.trailing_delta)
                    if new_stop < order.stop_price:
                        order.stop_price = new_stop
                    elif price >= order.stop_price:
                        self._execute_fill(
                            order, self._market_fill_price(order.side, price), release_first=True
                        )
                        return True
        return False

    @staticmethod
    def _stop_triggered(order: Order, price: float) -> bool:
        """Stop-Auslösung: Buy-Stops über, Sell-Stops unter dem Stop-Preis."""
        assert order.stop_price is not None
        if order.side is OrderSide.BUY:
            return price >= order.stop_price
        return price <= order.stop_price

    def _reserve_for_order(self, order: Order) -> None:
        """Reserviert Guthaben für eine offene Order (Buy: Quote, Sell: Base)."""
        base, quote = _split_symbol(order.symbol)
        if order.side is OrderSide.BUY:
            reference = order.price or order.stop_price or self._last_price.get(order.symbol, 0.0)
            cost = order.amount * reference * (1.0 + self._config.commission_rate)
            self._check_free(quote, cost)
            self._reserved[quote] = self._reserved.get(quote, 0.0) + cost
        else:
            if not order.reduce_only:
                self._check_free(base, order.amount)
            self._reserved[base] = self._reserved.get(base, 0.0) + min(
                order.amount, self._balances.get(base, 0.0)
            )

    def _release_reservation(self, order: Order) -> None:
        """Gibt die für eine Order reservierten Mittel wieder frei."""
        base, quote = _split_symbol(order.symbol)
        if order.side is OrderSide.BUY:
            reference = order.price or order.stop_price or self._last_price.get(order.symbol, 0.0)
            cost = order.remaining * reference * (1.0 + self._config.commission_rate)
            self._reserved[quote] = max(self._reserved.get(quote, 0.0) - cost, 0.0)
        else:
            self._reserved[base] = max(self._reserved.get(base, 0.0) - order.remaining, 0.0)

    def _check_free(self, currency: str, amount: float) -> None:
        """Wirft :class:`InsufficientFundsError`, wenn das freie Guthaben nicht reicht."""
        free = self._balances.get(currency, 0.0) - self._reserved.get(currency, 0.0)
        if amount > free + 1e-9:
            raise InsufficientFundsError(
                f"Unzureichendes Guthaben: benötigt {amount:.8f} {currency}, frei {free:.8f}"
            )

    def _execute_fill(self, order: Order, fill_price: float, release_first: bool = False) -> None:
        """Führt eine Order vollständig zum gegebenen Preis aus und verbucht sie."""
        if release_first:
            self._release_reservation(order)
        base, quote = _split_symbol(order.symbol)
        amount = order.remaining
        cost = amount * fill_price
        fee = cost * self._config.commission_rate

        if order.side is OrderSide.BUY:
            total = cost + fee
            self._check_free(quote, total)
            self._balances[quote] = self._balances.get(quote, 0.0) - total
            self._balances[base] = self._balances.get(base, 0.0) + amount
        else:
            if not order.reduce_only:
                self._check_free(base, amount)
            self._balances[base] = self._balances.get(base, 0.0) - amount
            self._balances[quote] = self._balances.get(quote, 0.0) + cost - fee

        order.record_fill(amount, fill_price, fee)
        logger.info(
            "Paper-Fill: %s %s %.8f %s @ %.8f (fee=%.8f %s)",
            order.side.value,
            order.symbol,
            amount,
            base,
            fill_price,
            fee,
            quote,
        )
