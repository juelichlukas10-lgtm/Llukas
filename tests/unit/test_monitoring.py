"""Unit-Tests für das Benachrichtigungssystem."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tradingbot.core.config import NotificationEventsConfig, NotificationsConfig
from tradingbot.core.enums import NotificationEvent, PositionSide
from tradingbot.core.events import EventBus, EventType
from tradingbot.core.models import Position, Trade
from tradingbot.monitoring.notifier import (
    DiscordNotifier,
    EmailNotifier,
    NotificationManager,
    Notifier,
    TelegramNotifier,
)


class RecordingNotifier(Notifier):
    """Test-Kanal, der alle Nachrichten aufzeichnet."""

    name = "recording"

    def __init__(self, fail: bool = False) -> None:
        self.messages: list[tuple[str, str]] = []
        self.fail = fail

    def is_configured(self) -> bool:
        return True

    async def send(self, title: str, message: str) -> None:
        if self.fail:
            from tradingbot.core.exceptions import NotificationError

            raise NotificationError("Testfehler")
        self.messages.append((title, message))


def _config(**event_overrides) -> NotificationsConfig:
    return NotificationsConfig(
        enabled=True,
        channels=[],
        events=NotificationEventsConfig(**event_overrides),
    )


def _trade(pnl: float = 5.0) -> Trade:
    now = datetime.now(timezone.utc)
    return Trade(
        symbol="BTC/USDT",
        side=PositionSide.LONG,
        amount=1.0,
        entry_price=100.0,
        exit_price=100.0 + pnl,
        pnl=pnl,
        fees=0.1,
        strategy="test",
        opened_at=now,
        closed_at=now,
    )


class TestChannelConfiguration:
    def test_unconfigured_channels_detected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for var in (
            "DISCORD_WEBHOOK_URL",
            "TELEGRAM_BOT_TOKEN",
            "TELEGRAM_CHAT_ID",
            "SMTP_HOST",
            "EMAIL_FROM",
            "EMAIL_TO",
        ):
            monkeypatch.delenv(var, raising=False)
        assert not DiscordNotifier().is_configured()
        assert not TelegramNotifier().is_configured()
        assert not EmailNotifier().is_configured()

    def test_configured_channels_detected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.test/webhook")
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
        assert DiscordNotifier().is_configured()
        assert TelegramNotifier().is_configured()

    def test_manager_skips_unconfigured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
        config = NotificationsConfig(enabled=True, channels=["discord"])
        manager = NotificationManager(config)
        assert manager.active_channels == []


class TestNotificationManager:
    async def test_position_opened_event(self) -> None:
        recorder = RecordingNotifier()
        manager = NotificationManager(_config(), notifiers=[recorder])
        bus = EventBus()
        manager.attach(bus)

        position = Position(
            symbol="BTC/USDT", side=PositionSide.LONG, amount=1.0, entry_price=100.0
        )
        await bus.publish(EventType.POSITION_OPENED, position)

        assert len(recorder.messages) == 1
        title, message = recorder.messages[0]
        assert "BTC/USDT" in title
        assert "long" in message

    async def test_trade_closed_event(self) -> None:
        recorder = RecordingNotifier()
        manager = NotificationManager(_config(), notifiers=[recorder])
        bus = EventBus()
        manager.attach(bus)

        await bus.publish(EventType.TRADE_CLOSED, _trade(pnl=5.0))
        assert len(recorder.messages) == 1
        assert "✅" in recorder.messages[0][0]

        await bus.publish(EventType.TRADE_CLOSED, _trade(pnl=-5.0))
        assert "❌" in recorder.messages[1][0]

    async def test_disabled_event_suppressed(self) -> None:
        recorder = RecordingNotifier()
        manager = NotificationManager(
            _config(trade_closed=False), notifiers=[recorder]
        )
        bus = EventBus()
        manager.attach(bus)
        await bus.publish(EventType.TRADE_CLOSED, _trade())
        assert recorder.messages == []

    async def test_channel_failure_does_not_block_others(self) -> None:
        failing = RecordingNotifier(fail=True)
        working = RecordingNotifier()
        manager = NotificationManager(_config(), notifiers=[failing, working])
        await manager.notify(NotificationEvent.ERROR, "Titel", "Text")
        assert len(working.messages) == 1

    async def test_risk_limit_event_classification(self) -> None:
        recorder = RecordingNotifier()
        manager = NotificationManager(_config(), notifiers=[recorder])
        bus = EventBus()
        manager.attach(bus)

        await bus.publish(EventType.RISK_LIMIT_HIT, "Max. Drawdown erreicht: 15%")
        await bus.publish(EventType.RISK_LIMIT_HIT, "Max. Tagesverlust erreicht: 3%")
        assert len(recorder.messages) == 2

    async def test_error_event(self) -> None:
        recorder = RecordingNotifier()
        manager = NotificationManager(_config(), notifiers=[recorder])
        bus = EventBus()
        manager.attach(bus)
        await bus.publish(EventType.ERROR, "Exchange nicht erreichbar")
        assert "Exchange nicht erreichbar" in recorder.messages[0][1]
