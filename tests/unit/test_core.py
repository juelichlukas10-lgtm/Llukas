"""Unit-Tests für das Core-Modul (Enums, Modelle, Config, Events)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from tradingbot.core.config import Config, load_config, load_credentials
from tradingbot.core.enums import (
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
    SignalAction,
    Timeframe,
    TradingMode,
)
from tradingbot.core.events import EventBus, EventType
from tradingbot.core.exceptions import ConfigError
from tradingbot.core.models import Order, Position, Signal, Trade


class TestTimeframe:
    def test_seconds(self) -> None:
        assert Timeframe.M1.seconds == 60
        assert Timeframe.H4.seconds == 14400
        assert Timeframe.D1.seconds == 86400

    def test_from_string_valid(self) -> None:
        assert Timeframe.from_string("15m") is Timeframe.M15

    def test_from_string_invalid(self) -> None:
        with pytest.raises(ValueError, match="Unbekannter Timeframe"):
            Timeframe.from_string("2h")

    def test_pandas_freq(self) -> None:
        assert Timeframe.M5.pandas_freq == "5min"
        assert Timeframe.D1.pandas_freq == "1D"


class TestEnums:
    def test_order_side_opposite(self) -> None:
        assert OrderSide.BUY.opposite is OrderSide.SELL
        assert OrderSide.SELL.opposite is OrderSide.BUY

    def test_position_close_side(self) -> None:
        assert PositionSide.LONG.close_side is OrderSide.SELL
        assert PositionSide.SHORT.close_side is OrderSide.BUY

    def test_terminal_status(self) -> None:
        assert OrderStatus.FILLED.is_terminal
        assert OrderStatus.CANCELED.is_terminal
        assert not OrderStatus.OPEN.is_terminal
        assert not OrderStatus.PARTIALLY_FILLED.is_terminal


class TestOrder:
    def test_partial_and_full_fill(self) -> None:
        order = Order(symbol="BTC/USDT", side=OrderSide.BUY, type=OrderType.LIMIT, amount=2.0, price=100.0)
        order.record_fill(0.5, 100.0, fee=0.05)
        assert order.status is OrderStatus.PARTIALLY_FILLED
        assert order.remaining == pytest.approx(1.5)

        order.record_fill(1.5, 102.0, fee=0.15)
        assert order.status is OrderStatus.FILLED
        assert order.remaining == pytest.approx(0.0)
        assert order.average_price == pytest.approx((0.5 * 100 + 1.5 * 102) / 2.0)
        assert order.fee == pytest.approx(0.2)

    def test_overfill_raises(self) -> None:
        order = Order(symbol="BTC/USDT", side=OrderSide.BUY, type=OrderType.MARKET, amount=1.0)
        with pytest.raises(ValueError, match="überschreitet"):
            order.record_fill(1.5, 100.0)

    def test_zero_fill_raises(self) -> None:
        order = Order(symbol="BTC/USDT", side=OrderSide.BUY, type=OrderType.MARKET, amount=1.0)
        with pytest.raises(ValueError, match="positiv"):
            order.record_fill(0.0, 100.0)


class TestPosition:
    def test_long_pnl(self) -> None:
        pos = Position(symbol="BTC/USDT", side=PositionSide.LONG, amount=2.0, entry_price=100.0)
        assert pos.unrealized_pnl(110.0) == pytest.approx(20.0)
        assert pos.unrealized_pnl_pct(110.0) == pytest.approx(0.10)

    def test_short_pnl(self) -> None:
        pos = Position(symbol="BTC/USDT", side=PositionSide.SHORT, amount=2.0, entry_price=100.0)
        assert pos.unrealized_pnl(90.0) == pytest.approx(20.0)
        assert pos.unrealized_pnl_pct(90.0) == pytest.approx(0.10)

    def test_leverage_pnl_pct(self) -> None:
        pos = Position(
            symbol="BTC/USDT", side=PositionSide.LONG, amount=1.0, entry_price=100.0, leverage=5.0
        )
        assert pos.unrealized_pnl_pct(102.0) == pytest.approx(0.10)

    def test_extremes_tracking(self) -> None:
        pos = Position(symbol="BTC/USDT", side=PositionSide.LONG, amount=1.0, entry_price=100.0)
        pos.update_extremes(105.0)
        pos.update_extremes(95.0)
        pos.update_extremes(103.0)
        assert pos.highest_price == pytest.approx(105.0)
        assert pos.lowest_price == pytest.approx(95.0)


class TestTrade:
    def test_pnl_pct_and_win(self) -> None:
        now = datetime.now(timezone.utc)
        trade = Trade(
            symbol="BTC/USDT",
            side=PositionSide.LONG,
            amount=1.0,
            entry_price=100.0,
            exit_price=105.0,
            pnl=5.0,
            fees=0.2,
            strategy="test",
            opened_at=now,
            closed_at=now,
        )
        assert trade.is_win
        assert trade.pnl_pct == pytest.approx(0.05)


class TestSignal:
    def test_entry_exit_classification(self) -> None:
        now = datetime.now(timezone.utc)
        buy = Signal(action=SignalAction.BUY, symbol="BTC/USDT", strategy="s", timestamp=now, price=1.0)
        close = Signal(
            action=SignalAction.CLOSE_LONG, symbol="BTC/USDT", strategy="s", timestamp=now, price=1.0
        )
        hold = Signal(action=SignalAction.HOLD, symbol="BTC/USDT", strategy="s", timestamp=now, price=1.0)
        assert buy.is_entry and not buy.is_exit
        assert close.is_exit and not close.is_entry
        assert not hold.is_entry and not hold.is_exit


class TestConfig:
    def test_load_valid_config(self, config_yaml: Path) -> None:
        config = load_config(config_yaml, env_file=None)
        assert config.app.name == "TestBot"
        assert config.trading.mode is TradingMode.PAPER
        assert config.trading.timeframe is Timeframe.M5
        assert config.strategies.active[0].name == "ema_crossover"

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="nicht gefunden"):
            load_config(tmp_path / "missing.yaml", env_file=None)

    def test_live_without_confirmation_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "cfg.yaml"
        path.write_text("trading:\n  mode: live\n", encoding="utf-8")
        with pytest.raises(ConfigError, match="live_trading_confirmed"):
            load_config(path, env_file=None)

    def test_live_with_confirmation_ok(self, tmp_path: Path) -> None:
        path = tmp_path / "cfg.yaml"
        path.write_text(
            "trading:\n  mode: live\n  live_trading_confirmed: true\n", encoding="utf-8"
        )
        config = load_config(path, env_file=None)
        assert config.trading.mode is TradingMode.LIVE

    def test_db_url_env_override(self, config_yaml: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRADINGBOT_DB_URL", "sqlite:///other.db")
        config = load_config(config_yaml, env_file=None)
        assert config.database.url == "sqlite:///other.db"

    def test_credentials_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BINANCE_API_KEY", "key123")
        monkeypatch.setenv("BINANCE_API_SECRET", "secret456")
        creds = load_credentials("binance")
        assert creds.is_configured
        assert creds.api_key == "key123"

    def test_credentials_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("KRAKEN_API_KEY", raising=False)
        monkeypatch.delenv("KRAKEN_API_SECRET", raising=False)
        creds = load_credentials("kraken")
        assert not creds.is_configured

    def test_defaults(self) -> None:
        config = Config()
        assert config.trading.mode is TradingMode.PAPER
        assert config.risk.max_open_positions == 5
        assert config.database.url.startswith("sqlite")


class TestEventBus:
    async def test_sync_and_async_handlers(self) -> None:
        bus = EventBus()
        received: list[object] = []

        def sync_handler(payload: object) -> None:
            received.append(("sync", payload))

        async def async_handler(payload: object) -> None:
            received.append(("async", payload))

        bus.subscribe(EventType.SIGNAL, sync_handler)
        bus.subscribe(EventType.SIGNAL, async_handler)
        await bus.publish(EventType.SIGNAL, "payload")

        assert ("sync", "payload") in received
        assert ("async", "payload") in received

    async def test_handler_exception_does_not_break_bus(self) -> None:
        bus = EventBus()
        received: list[object] = []

        def failing(_: object) -> None:
            raise RuntimeError("boom")

        bus.subscribe(EventType.ERROR, failing)
        bus.subscribe(EventType.ERROR, received.append)
        await bus.publish(EventType.ERROR, "x")
        assert received == ["x"]

    async def test_unsubscribe(self) -> None:
        bus = EventBus()
        received: list[object] = []
        bus.subscribe(EventType.CANDLE, received.append)
        bus.unsubscribe(EventType.CANDLE, received.append)
        await bus.publish(EventType.CANDLE, "c")
        assert received == []
