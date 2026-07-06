"""Unit-Tests für OrderManager und ExecutionEngine (gegen Paper-Exchange)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tradingbot.core.config import PaperConfig, RiskConfig
from tradingbot.core.enums import OrderSide, OrderStatus, OrderType, PositionSide, SignalAction
from tradingbot.core.events import EventBus, EventType
from tradingbot.core.models import Order, Signal
from tradingbot.exchange.paper import PaperExchangeAdapter
from tradingbot.execution.engine import ExecutionEngine
from tradingbot.execution.order_manager import OrderManager
from tradingbot.risk.manager import RiskDecision, RiskManager


@pytest.fixture()
def paper() -> PaperExchangeAdapter:
    config = PaperConfig(
        initial_balance=100_000.0, commission_rate=0.001, slippage_rate=0.0, spread_rate=0.0
    )
    adapter = PaperExchangeAdapter(config=config)
    adapter.set_price("BTC/USDT", 100.0)
    return adapter


@pytest.fixture()
def setup(paper: PaperExchangeAdapter):
    """Komplett verdrahteter Execution-Stack gegen den Paper-Exchange."""
    bus = EventBus()
    order_manager = OrderManager(paper, limit_order_timeout_seconds=0)
    risk = RiskManager(RiskConfig(trailing_stop=0.0, break_even_trigger=0.0), 100_000.0)
    engine = ExecutionEngine(order_manager, risk, bus)
    return paper, bus, order_manager, risk, engine


def _entry_signal(action: SignalAction = SignalAction.BUY, price: float = 100.0) -> Signal:
    return Signal(
        action=action,
        symbol="BTC/USDT",
        strategy="test",
        timestamp=datetime.now(timezone.utc),
        price=price,
    )


class TestOrderManager:
    async def test_submit_market_fills(self, paper: PaperExchangeAdapter) -> None:
        manager = OrderManager(paper)
        order = await manager.submit_market("BTC/USDT", OrderSide.BUY, 1.0)
        assert order.status is OrderStatus.FILLED
        assert order.average_price == pytest.approx(100.0)
        assert manager.open_orders == []

    async def test_open_limit_is_tracked_and_cancelable(self, paper: PaperExchangeAdapter) -> None:
        manager = OrderManager(paper)
        order = Order(
            symbol="BTC/USDT", side=OrderSide.BUY, type=OrderType.LIMIT, amount=1.0, price=90.0
        )
        await manager.submit(order)
        assert len(manager.open_orders) == 1

        count = await manager.cancel_all()
        assert count == 1
        assert manager.open_orders == []

    async def test_sync_detects_fill(self, paper: PaperExchangeAdapter) -> None:
        manager = OrderManager(paper)
        order = Order(
            symbol="BTC/USDT", side=OrderSide.BUY, type=OrderType.LIMIT, amount=1.0, price=95.0
        )
        await manager.submit(order)
        paper.set_price("BTC/USDT", 94.0)  # Limit wird gefüllt

        filled = await manager.sync_open_orders()
        assert len(filled) == 1
        assert filled[0].status is OrderStatus.FILLED
        assert manager.open_orders == []

    async def test_update_callback_invoked(self, paper: PaperExchangeAdapter) -> None:
        updates: list[Order] = []
        manager = OrderManager(paper, on_order_update=updates.append)
        await manager.submit_market("BTC/USDT", OrderSide.BUY, 1.0)
        assert len(updates) == 1

    async def test_zero_amount_rejected(self, paper: PaperExchangeAdapter) -> None:
        from tradingbot.core.exceptions import OrderError

        manager = OrderManager(paper)
        with pytest.raises(OrderError):
            await manager.submit_market("BTC/USDT", OrderSide.BUY, 0.0)


class TestExecutionEngine:
    async def test_entry_creates_position(self, setup) -> None:
        paper, bus, _, _, engine = setup
        opened: list[object] = []
        bus.subscribe(EventType.POSITION_OPENED, opened.append)

        decision = RiskDecision(approved=True, stop_loss=95.0, take_profit=110.0)
        position = await engine.execute_entry(_entry_signal(), amount=2.0, decision=decision)

        assert position is not None
        assert position.side is PositionSide.LONG
        assert position.stop_loss == pytest.approx(95.0)
        assert engine.position_side("BTC/USDT") is PositionSide.LONG
        assert engine.open_position_count() == 1
        assert len(opened) == 1

    async def test_duplicate_entry_skipped(self, setup) -> None:
        _, _, _, _, engine = setup
        decision = RiskDecision(approved=True)
        await engine.execute_entry(_entry_signal(), amount=1.0, decision=decision)
        second = await engine.execute_entry(_entry_signal(), amount=1.0, decision=decision)
        assert second is None
        assert engine.open_position_count() == 1

    async def test_close_position_realizes_pnl(self, setup) -> None:
        paper, bus, _, _, engine = setup
        closed: list[object] = []
        bus.subscribe(EventType.TRADE_CLOSED, closed.append)

        await engine.execute_entry(_entry_signal(), amount=2.0, decision=RiskDecision(approved=True))
        paper.set_price("BTC/USDT", 110.0)
        trade = await engine.close_position("BTC/USDT", reason="signal")
        await engine.publish_trade(trade)

        assert trade is not None
        # Brutto 2*(110-100)=20, Gebühren: Entry 0.2 + Exit 0.22
        assert trade.pnl == pytest.approx(20.0 - 0.2 - 0.22)
        assert trade.exit_reason == "signal"
        assert engine.open_position_count() == 0
        assert len(closed) == 1

    async def test_partial_close(self, setup) -> None:
        paper, _, _, _, engine = setup
        await engine.execute_entry(_entry_signal(), amount=4.0, decision=RiskDecision(approved=True))
        paper.set_price("BTC/USDT", 105.0)

        trade = await engine.close_position("BTC/USDT", reason="take_profit", portion=0.5)
        assert trade is not None
        assert trade.amount == pytest.approx(2.0)
        # Restposition bleibt offen.
        assert engine.open_position_count() == 1
        assert engine.positions["BTC/USDT"].amount == pytest.approx(2.0)

    async def test_stop_loss_exit_on_price_update(self, setup) -> None:
        paper, _, _, _, engine = setup
        decision = RiskDecision(approved=True, stop_loss=95.0)
        await engine.execute_entry(_entry_signal(), amount=1.0, decision=decision)

        assert await engine.on_price_update("BTC/USDT", 96.0) is None

        paper.set_price("BTC/USDT", 94.0)
        trade = await engine.on_price_update("BTC/USDT", 94.0)
        assert trade is not None
        assert trade.exit_reason == "stop_loss"
        assert engine.open_position_count() == 0

    async def test_take_profit_exit(self, setup) -> None:
        paper, _, _, _, engine = setup
        decision = RiskDecision(approved=True, take_profit=105.0)
        await engine.execute_entry(_entry_signal(), amount=1.0, decision=decision)

        paper.set_price("BTC/USDT", 106.0)
        trade = await engine.on_price_update("BTC/USDT", 106.0)
        assert trade is not None
        assert trade.exit_reason == "take_profit"
        assert trade.pnl > 0

    async def test_trailing_stop_via_risk_manager(self, paper: PaperExchangeAdapter) -> None:
        bus = EventBus()
        order_manager = OrderManager(paper)
        risk = RiskManager(RiskConfig(trailing_stop=0.05), 100_000.0)
        engine = ExecutionEngine(order_manager, risk, bus)

        decision = RiskDecision(approved=True, stop_loss=90.0, trailing_stop=0.05)
        await engine.execute_entry(_entry_signal(), amount=1.0, decision=decision)

        # Kurs steigt: Trailing zieht SL nach.
        paper.set_price("BTC/USDT", 120.0)
        assert await engine.on_price_update("BTC/USDT", 120.0) is None
        assert engine.positions["BTC/USDT"].stop_loss == pytest.approx(114.0)

        # Rückgang unter den nachgezogenen Stop -> Exit mit Gewinn.
        paper.set_price("BTC/USDT", 113.0)
        trade = await engine.on_price_update("BTC/USDT", 113.0)
        assert trade is not None
        assert trade.exit_reason == "stop_loss"
        assert trade.pnl > 0

    async def test_close_all(self, setup) -> None:
        paper, _, _, _, engine = setup
        paper.set_price("ETH/USDT", 50.0)
        await engine.execute_entry(_entry_signal(), amount=1.0, decision=RiskDecision(approved=True))
        eth_signal = Signal(
            action=SignalAction.BUY,
            symbol="ETH/USDT",
            strategy="test",
            timestamp=datetime.now(timezone.utc),
            price=50.0,
        )
        await engine.execute_entry(eth_signal, amount=2.0, decision=RiskDecision(approved=True))
        assert engine.open_position_count() == 2

        trades = await engine.close_all(reason="shutdown")
        assert len(trades) == 2
        assert engine.open_position_count() == 0
        assert all(t.exit_reason == "shutdown" for t in trades)

    async def test_close_without_position_returns_none(self, setup) -> None:
        _, _, _, _, engine = setup
        assert await engine.close_position("BTC/USDT") is None
