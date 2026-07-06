"""Wiederverwendbarer Mock-Exchange für Unit- und Integrationstests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator

from tradingbot.core.enums import OrderSide, OrderStatus, Timeframe
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


class MockExchangeAdapter(ExchangeAdapter):
    """In-Memory-Exchange mit deterministischen Daten für Tests.

    * Kerzen werden aus einer vorgegebenen Preisliste generiert.
    * Orders werden sofort vollständig zum aktuellen Preis gefüllt.
    * Alle Aufrufe werden für Assertions aufgezeichnet.
    """

    name = "mock"

    def __init__(self, prices: list[float] | None = None, balance: float = 10_000.0) -> None:
        self.prices = prices or [100.0 + i for i in range(300)]
        self.current_price = self.prices[-1]
        self.balance = balance
        self.created_orders: list[Order] = []
        self.canceled_orders: list[Order] = []
        self.connected = False

    async def connect(self) -> None:
        self.connected = True

    async def close(self) -> None:
        self.connected = False

    async def fetch_ticker(self, symbol: str) -> Ticker:
        spread = self.current_price * 0.0001
        return Ticker(
            symbol=symbol,
            timestamp=datetime.now(timezone.utc),
            bid=self.current_price - spread,
            ask=self.current_price + spread,
            last=self.current_price,
        )

    async def fetch_order_book(self, symbol: str, limit: int = 25) -> OrderBook:
        p = self.current_price
        return OrderBook(
            symbol=symbol,
            timestamp=datetime.now(timezone.utc),
            bids=tuple(OrderBookLevel(p - 0.1 * (i + 1), 1.0) for i in range(limit)),
            asks=tuple(OrderBookLevel(p + 0.1 * (i + 1), 1.0) for i in range(limit)),
        )

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: Timeframe,
        since: int | None = None,
        limit: int = 500,
    ) -> list[Candle]:
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        candles: list[Candle] = []
        for i, price in enumerate(self.prices[-limit:]):
            ts = start + timedelta(seconds=timeframe.seconds * i)
            if since is not None and ts.timestamp() * 1000 < since:
                continue
            candles.append(
                Candle(
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp=ts,
                    open=price * 0.999,
                    high=price * 1.001,
                    low=price * 0.998,
                    close=price,
                    volume=100.0,
                )
            )
        return candles

    async def fetch_trades(self, symbol: str, limit: int = 100) -> list[TradeTick]:
        return [
            TradeTick(
                symbol=symbol,
                timestamp=datetime.now(timezone.utc),
                price=self.current_price,
                amount=1.0,
                side=OrderSide.BUY,
            )
        ]

    async def fetch_funding_rate(self, symbol: str) -> FundingRate | None:
        return None

    async def fetch_open_interest(self, symbol: str) -> OpenInterest | None:
        return None

    async def fetch_balance(self) -> dict[str, Balance]:
        return {"USDT": Balance(currency="USDT", free=self.balance, used=0.0)}

    async def create_order(self, order: Order) -> Order:
        order.exchange_id = f"mock-{len(self.created_orders)}"
        order.record_fill(order.amount, self.current_price)
        self.created_orders.append(order)
        return order

    async def cancel_order(self, order: Order) -> Order:
        order.status = OrderStatus.CANCELED
        self.canceled_orders.append(order)
        return order

    async def fetch_order(self, order: Order) -> Order:
        return order

    async def fetch_open_orders(self, symbol: str | None = None) -> list[Order]:
        return []

    async def set_leverage(self, symbol: str, leverage: float) -> None:
        pass

    async def watch_ticker(self, symbol: str) -> AsyncIterator[Ticker]:
        for price in self.prices:
            self.current_price = price
            yield await self.fetch_ticker(symbol)

    async def watch_candles(self, symbol: str, timeframe: Timeframe) -> AsyncIterator[Candle]:
        for candle in await self.fetch_ohlcv(symbol, timeframe, limit=len(self.prices)):
            yield candle

    async def watch_order_book(self, symbol: str, limit: int = 25) -> AsyncIterator[OrderBook]:
        yield await self.fetch_order_book(symbol, limit)

    async def watch_trades(self, symbol: str) -> AsyncIterator[TradeTick]:
        for trade in await self.fetch_trades(symbol):
            yield trade

    def market_info(self, symbol: str) -> dict[str, Any]:
        base, _, quote = symbol.partition("/")
        return {
            "symbol": symbol,
            "base": base,
            "quote": quote,
            "precision": {"amount": 8, "price": 8},
            "limits": {"amount": {"min": 0.0}, "cost": {"min": 0.0}},
        }
