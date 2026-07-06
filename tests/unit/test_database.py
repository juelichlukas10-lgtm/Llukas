"""Unit-Tests für die Persistenzschicht (In-Memory-SQLite)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tradingbot.core.enums import OrderSide, OrderType, PositionSide, Timeframe
from tradingbot.core.models import Order, Position, Trade
from tradingbot.database.repository import Database


@pytest.fixture()
def db() -> Database:
    database = Database(url="sqlite:///:memory:")
    yield database
    database.close()


def _make_trade(pnl: float = 5.0, strategy: str = "ema_crossover") -> Trade:
    now = datetime.now(timezone.utc)
    return Trade(
        symbol="BTC/USDT",
        side=PositionSide.LONG,
        amount=1.0,
        entry_price=100.0,
        exit_price=100.0 + pnl,
        pnl=pnl,
        fees=0.2,
        strategy=strategy,
        opened_at=now - timedelta(hours=1),
        closed_at=now,
    )


class TestTrades:
    def test_save_and_load(self, db: Database) -> None:
        trade = _make_trade()
        db.save_trade(trade)
        records = db.get_trades()
        assert len(records) == 1
        assert records[0].id == trade.id
        assert records[0].pnl == pytest.approx(5.0)

    def test_filters(self, db: Database) -> None:
        db.save_trade(_make_trade(strategy="rsi"))
        db.save_trade(_make_trade(strategy="macd"))
        assert len(db.get_trades(strategy="rsi")) == 1
        assert len(db.get_trades(symbol="ETH/USDT")) == 0
        assert len(db.get_trades(limit=1)) == 1


class TestOrders:
    def test_save_order_upsert(self, db: Database) -> None:
        order = Order(
            symbol="BTC/USDT", side=OrderSide.BUY, type=OrderType.LIMIT, amount=1.0, price=100.0
        )
        db.save_order(order)
        order.record_fill(1.0, 100.0)
        db.save_order(order)

        records = db.get_orders()
        assert len(records) == 1
        assert records[0].status == "filled"
        assert records[0].filled == pytest.approx(1.0)


class TestPositions:
    def test_lifecycle(self, db: Database) -> None:
        position = Position(
            symbol="BTC/USDT", side=PositionSide.LONG, amount=1.0, entry_price=100.0
        )
        db.save_position(position)
        assert len(db.get_positions()) == 1

        db.delete_position(position.id)
        assert db.get_positions() == []


class TestPerformance:
    def test_snapshots_ordered(self, db: Database) -> None:
        base = datetime.now(timezone.utc)
        db.save_performance_snapshot(equity=10_100.0, balance=10_000.0, timestamp=base)
        db.save_performance_snapshot(
            equity=10_200.0, balance=10_000.0, timestamp=base + timedelta(minutes=5)
        )
        history = db.get_performance_history()
        assert len(history) == 2
        assert history[0].equity < history[1].equity


class TestStrategies:
    def test_upsert(self, db: Database) -> None:
        db.upsert_strategy("ema", {"fast": 12}, ["BTC/USDT"], Timeframe.M5)
        db.upsert_strategy("ema", {"fast": 20}, ["BTC/USDT", "ETH/USDT"], Timeframe.M15)
        strategies = db.get_strategies()
        assert len(strategies) == 1
        assert strategies[0].params == {"fast": 20}
        assert strategies[0].timeframe == "15m"


class TestErrorLogs:
    def test_log_with_exception(self, db: Database) -> None:
        try:
            raise ValueError("kaputt")
        except ValueError as exc:
            db.log_error("Etwas ging schief", module="test", exc=exc)
        logs = db.get_error_logs()
        assert len(logs) == 1
        assert "kaputt" in logs[0].traceback
        assert logs[0].module == "test"


class TestBacktests:
    def test_save_and_query(self, db: Database) -> None:
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 6, 1, tzinfo=timezone.utc)
        backtest_id = db.save_backtest(
            strategy="ema_crossover",
            symbols=["BTC/USDT"],
            timeframe=Timeframe.H1,
            params={"fast_period": 12},
            start=start,
            end=end,
            initial_balance=10_000.0,
            final_equity=12_345.0,
            metrics={"sharpe_ratio": 1.5, "max_drawdown": 0.08},
        )
        records = db.get_backtests(strategy="ema_crossover")
        assert len(records) == 1
        assert records[0].id == backtest_id
        assert records[0].metrics["sharpe_ratio"] == pytest.approx(1.5)
        assert db.get_backtests(strategy="unknown") == []
