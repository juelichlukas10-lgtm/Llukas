"""Unit-Tests für den Paper-Trading-Exchange."""

from __future__ import annotations

import pytest

from tradingbot.core.config import PaperConfig
from tradingbot.core.enums import OrderSide, OrderStatus, OrderType
from tradingbot.core.exceptions import DataError, InsufficientFundsError, OrderError
from tradingbot.core.models import Order
from tradingbot.exchange.paper import PaperExchangeAdapter


@pytest.fixture()
def paper() -> PaperExchangeAdapter:
    config = PaperConfig(
        initial_balance=10_000.0, commission_rate=0.001, slippage_rate=0.0, spread_rate=0.0
    )
    adapter = PaperExchangeAdapter(config=config, quote_currency="USDT")
    adapter.set_price("BTC/USDT", 100.0)
    return adapter


class TestMarketOrders:
    async def test_market_buy_updates_balances(self, paper: PaperExchangeAdapter) -> None:
        order = Order(symbol="BTC/USDT", side=OrderSide.BUY, type=OrderType.MARKET, amount=10.0)
        await paper.create_order(order)

        assert order.status is OrderStatus.FILLED
        assert order.average_price == pytest.approx(100.0)
        balances = await paper.fetch_balance()
        # 10 * 100 = 1000 Kosten + 1 Fee
        assert balances["USDT"].total == pytest.approx(10_000.0 - 1001.0)
        assert balances["BTC"].total == pytest.approx(10.0)

    async def test_market_sell_roundtrip(self, paper: PaperExchangeAdapter) -> None:
        buy = Order(symbol="BTC/USDT", side=OrderSide.BUY, type=OrderType.MARKET, amount=10.0)
        await paper.create_order(buy)
        paper.set_price("BTC/USDT", 110.0)
        sell = Order(symbol="BTC/USDT", side=OrderSide.SELL, type=OrderType.MARKET, amount=10.0)
        await paper.create_order(sell)

        balances = await paper.fetch_balance()
        assert balances["BTC"].total == pytest.approx(0.0)
        # 10000 - 1001 + 1100 - 1.1 = 10097.9
        assert balances["USDT"].total == pytest.approx(10_097.9)

    async def test_slippage_and_spread_applied(self) -> None:
        config = PaperConfig(
            initial_balance=10_000.0, commission_rate=0.0, slippage_rate=0.001, spread_rate=0.002
        )
        paper = PaperExchangeAdapter(config=config)
        paper.set_price("BTC/USDT", 100.0)
        order = Order(symbol="BTC/USDT", side=OrderSide.BUY, type=OrderType.MARKET, amount=1.0)
        await paper.create_order(order)
        # 100 * (1 + 0.002/2 + 0.001) = 100.2
        assert order.average_price == pytest.approx(100.2)

    async def test_insufficient_funds(self, paper: PaperExchangeAdapter) -> None:
        order = Order(symbol="BTC/USDT", side=OrderSide.BUY, type=OrderType.MARKET, amount=1000.0)
        with pytest.raises(InsufficientFundsError):
            await paper.create_order(order)

    async def test_no_price_raises(self) -> None:
        paper = PaperExchangeAdapter()
        order = Order(symbol="ETH/USDT", side=OrderSide.BUY, type=OrderType.MARKET, amount=1.0)
        with pytest.raises(DataError):
            await paper.create_order(order)


class TestPendingOrders:
    async def test_limit_buy_fills_when_price_drops(self, paper: PaperExchangeAdapter) -> None:
        order = Order(
            symbol="BTC/USDT", side=OrderSide.BUY, type=OrderType.LIMIT, amount=5.0, price=95.0
        )
        await paper.create_order(order)
        assert order.status is OrderStatus.OPEN

        paper.set_price("BTC/USDT", 96.0)
        assert order.status is OrderStatus.OPEN

        paper.set_price("BTC/USDT", 94.0)
        assert order.status is OrderStatus.FILLED
        assert order.average_price == pytest.approx(95.0)

    async def test_limit_requires_price(self, paper: PaperExchangeAdapter) -> None:
        order = Order(symbol="BTC/USDT", side=OrderSide.BUY, type=OrderType.LIMIT, amount=1.0)
        with pytest.raises(OrderError):
            await paper.create_order(order)

    async def test_stop_market_sell_triggers(self, paper: PaperExchangeAdapter) -> None:
        buy = Order(symbol="BTC/USDT", side=OrderSide.BUY, type=OrderType.MARKET, amount=5.0)
        await paper.create_order(buy)

        stop = Order(
            symbol="BTC/USDT",
            side=OrderSide.SELL,
            type=OrderType.STOP_MARKET,
            amount=5.0,
            stop_price=90.0,
        )
        await paper.create_order(stop)
        assert stop.status is OrderStatus.OPEN

        paper.set_price("BTC/USDT", 89.0)
        assert stop.status is OrderStatus.FILLED

    async def test_stop_limit_becomes_limit(self, paper: PaperExchangeAdapter) -> None:
        buy = Order(symbol="BTC/USDT", side=OrderSide.BUY, type=OrderType.MARKET, amount=5.0)
        await paper.create_order(buy)

        stop_limit = Order(
            symbol="BTC/USDT",
            side=OrderSide.SELL,
            type=OrderType.STOP_LIMIT,
            amount=5.0,
            stop_price=90.0,
            price=89.5,
        )
        await paper.create_order(stop_limit)
        paper.set_price("BTC/USDT", 90.0)
        assert stop_limit.status is OrderStatus.FILLED
        assert stop_limit.average_price == pytest.approx(89.5)

    async def test_trailing_stop_follows_price_up(self, paper: PaperExchangeAdapter) -> None:
        buy = Order(symbol="BTC/USDT", side=OrderSide.BUY, type=OrderType.MARKET, amount=5.0)
        await paper.create_order(buy)

        trailing = Order(
            symbol="BTC/USDT",
            side=OrderSide.SELL,
            type=OrderType.TRAILING_STOP,
            amount=5.0,
            trailing_delta=0.05,
        )
        await paper.create_order(trailing)
        assert trailing.stop_price == pytest.approx(95.0)

        paper.set_price("BTC/USDT", 120.0)
        assert trailing.status is OrderStatus.OPEN
        assert trailing.stop_price == pytest.approx(114.0)

        paper.set_price("BTC/USDT", 113.0)
        assert trailing.status is OrderStatus.FILLED

    async def test_cancel_releases_reservation(self, paper: PaperExchangeAdapter) -> None:
        order = Order(
            symbol="BTC/USDT", side=OrderSide.BUY, type=OrderType.LIMIT, amount=50.0, price=95.0
        )
        await paper.create_order(order)
        balances = await paper.fetch_balance()
        assert balances["USDT"].used > 0

        await paper.cancel_order(order)
        assert order.status is OrderStatus.CANCELED
        balances = await paper.fetch_balance()
        assert balances["USDT"].used == pytest.approx(0.0)
        assert await paper.fetch_open_orders() == []

    async def test_open_orders_filter_by_symbol(self, paper: PaperExchangeAdapter) -> None:
        paper.set_price("ETH/USDT", 50.0)
        o1 = Order(symbol="BTC/USDT", side=OrderSide.BUY, type=OrderType.LIMIT, amount=1.0, price=90.0)
        o2 = Order(symbol="ETH/USDT", side=OrderSide.BUY, type=OrderType.LIMIT, amount=1.0, price=45.0)
        await paper.create_order(o1)
        await paper.create_order(o2)

        btc_orders = await paper.fetch_open_orders("BTC/USDT")
        assert len(btc_orders) == 1
        assert btc_orders[0].symbol == "BTC/USDT"
        assert len(await paper.fetch_open_orders()) == 2

    async def test_reserved_funds_block_double_spending(self, paper: PaperExchangeAdapter) -> None:
        o1 = Order(
            symbol="BTC/USDT", side=OrderSide.BUY, type=OrderType.LIMIT, amount=90.0, price=99.0
        )
        await paper.create_order(o1)
        o2 = Order(
            symbol="BTC/USDT", side=OrderSide.BUY, type=OrderType.LIMIT, amount=90.0, price=99.0
        )
        with pytest.raises(InsufficientFundsError):
            await paper.create_order(o2)


class TestTickerWithoutProvider:
    async def test_synthetic_ticker(self, paper: PaperExchangeAdapter) -> None:
        ticker = await paper.fetch_ticker("BTC/USDT")
        assert ticker.last == pytest.approx(100.0)
        assert ticker.bid <= ticker.last <= ticker.ask

    async def test_market_info_fallback(self, paper: PaperExchangeAdapter) -> None:
        info = paper.market_info("BTC/USDT")
        assert info["base"] == "BTC"
        assert info["quote"] == "USDT"
