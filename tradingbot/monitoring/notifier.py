"""Benachrichtigungssystem (Discord, Telegram, E-Mail).

Der :class:`NotificationManager` abonniert die relevanten Ereignisse auf
dem Event-Bus und leitet sie – gemäß Konfiguration – an die aktivierten
Kanäle weiter. Zugangsdaten (Webhooks, Tokens, SMTP) kommen
ausschließlich aus Umgebungsvariablen.

Fehler beim Versand werden geloggt, unterbrechen aber niemals den
Handelsbetrieb.
"""

from __future__ import annotations

import asyncio
import os
import smtplib
from abc import ABC, abstractmethod
from email.mime.text import MIMEText

import aiohttp

from tradingbot.core.config import NotificationsConfig
from tradingbot.core.enums import NotificationEvent
from tradingbot.core.events import EventBus, EventType
from tradingbot.core.exceptions import NotificationError
from tradingbot.core.logging import get_logger
from tradingbot.core.models import Position, Trade

logger = get_logger(__name__)

#: HTTP-Timeout für Webhook-/API-Aufrufe (Sekunden).
_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=10)


class Notifier(ABC):
    """Abstrakter Benachrichtigungskanal."""

    #: Kanalname (entspricht der Konfiguration).
    name: str = "abstract"

    @abstractmethod
    async def send(self, title: str, message: str) -> None:
        """Versendet eine Nachricht.

        Args:
            title: Kurzer Titel/Betreff.
            message: Nachrichtentext (mehrzeilig erlaubt).

        Raises:
            NotificationError: Wenn der Versand fehlschlägt.
        """

    @abstractmethod
    def is_configured(self) -> bool:
        """True, wenn alle nötigen Zugangsdaten vorhanden sind."""


class DiscordNotifier(Notifier):
    """Versand über einen Discord-Webhook (``DISCORD_WEBHOOK_URL``)."""

    name = "discord"

    def __init__(self) -> None:
        self._webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "")

    def is_configured(self) -> bool:
        return bool(self._webhook_url)

    async def send(self, title: str, message: str) -> None:
        if not self.is_configured():
            raise NotificationError("DISCORD_WEBHOOK_URL ist nicht gesetzt")
        payload = {"embeds": [{"title": title, "description": message[:4000]}]}
        try:
            async with aiohttp.ClientSession(timeout=_HTTP_TIMEOUT) as session:
                async with session.post(self._webhook_url, json=payload) as response:
                    if response.status >= 400:
                        body = await response.text()
                        raise NotificationError(
                            f"Discord-Webhook antwortete mit {response.status}: {body[:200]}"
                        )
        except aiohttp.ClientError as exc:
            raise NotificationError(f"Discord-Versand fehlgeschlagen: {exc}") from exc


class TelegramNotifier(Notifier):
    """Versand über die Telegram-Bot-API.

    Benötigt ``TELEGRAM_BOT_TOKEN`` und ``TELEGRAM_CHAT_ID``.
    """

    name = "telegram"

    def __init__(self) -> None:
        self._token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self._chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    def is_configured(self) -> bool:
        return bool(self._token and self._chat_id)

    async def send(self, title: str, message: str) -> None:
        if not self.is_configured():
            raise NotificationError("TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID sind nicht gesetzt")
        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        payload = {"chat_id": self._chat_id, "text": f"{title}\n\n{message}"[:4096]}
        try:
            async with aiohttp.ClientSession(timeout=_HTTP_TIMEOUT) as session:
                async with session.post(url, json=payload) as response:
                    if response.status >= 400:
                        body = await response.text()
                        raise NotificationError(
                            f"Telegram-API antwortete mit {response.status}: {body[:200]}"
                        )
        except aiohttp.ClientError as exc:
            raise NotificationError(f"Telegram-Versand fehlgeschlagen: {exc}") from exc


class EmailNotifier(Notifier):
    """Versand per SMTP (STARTTLS).

    Benötigt ``SMTP_HOST``, ``SMTP_PORT``, ``SMTP_USER``, ``SMTP_PASSWORD``,
    ``EMAIL_FROM`` und ``EMAIL_TO``.
    """

    name = "email"

    def __init__(self) -> None:
        self._host = os.environ.get("SMTP_HOST", "")
        self._port = int(os.environ.get("SMTP_PORT", "587") or 587)
        self._user = os.environ.get("SMTP_USER", "")
        self._password = os.environ.get("SMTP_PASSWORD", "")
        self._from = os.environ.get("EMAIL_FROM", "")
        self._to = os.environ.get("EMAIL_TO", "")

    def is_configured(self) -> bool:
        return bool(self._host and self._from and self._to)

    def _send_sync(self, title: str, message: str) -> None:
        """Blockierender SMTP-Versand (läuft im Thread-Executor)."""
        mail = MIMEText(message, "plain", "utf-8")
        mail["Subject"] = title
        mail["From"] = self._from
        mail["To"] = self._to
        with smtplib.SMTP(self._host, self._port, timeout=15) as server:
            server.starttls()
            if self._user:
                server.login(self._user, self._password)
            server.sendmail(self._from, [self._to], mail.as_string())

    async def send(self, title: str, message: str) -> None:
        if not self.is_configured():
            raise NotificationError("SMTP-Konfiguration ist unvollständig")
        try:
            await asyncio.get_running_loop().run_in_executor(
                None, self._send_sync, title, message
            )
        except (smtplib.SMTPException, OSError) as exc:
            raise NotificationError(f"E-Mail-Versand fehlgeschlagen: {exc}") from exc


#: Registry der verfügbaren Kanal-Klassen.
_CHANNELS: dict[str, type[Notifier]] = {
    DiscordNotifier.name: DiscordNotifier,
    TelegramNotifier.name: TelegramNotifier,
    EmailNotifier.name: EmailNotifier,
}


class NotificationManager:
    """Verbindet Event-Bus und Benachrichtigungskanäle.

    Args:
        config: Benachrichtigungs-Konfiguration (Kanäle und Ereignisse).
        notifiers: Optionale vorgefertigte Kanal-Instanzen (Tests/DI);
            None = Kanäle gemäß Konfiguration instanziieren.
    """

    def __init__(
        self,
        config: NotificationsConfig,
        notifiers: list[Notifier] | None = None,
    ) -> None:
        self._config = config
        if notifiers is not None:
            self._notifiers = notifiers
        else:
            self._notifiers = []
            if config.enabled:
                for channel in config.channels:
                    notifier = _CHANNELS[channel]()
                    if notifier.is_configured():
                        self._notifiers.append(notifier)
                    else:
                        logger.warning(
                            "Benachrichtigungskanal '%s' aktiviert, aber nicht konfiguriert "
                            "(Umgebungsvariablen fehlen)",
                            channel,
                        )

    @property
    def active_channels(self) -> list[str]:
        """Namen der einsatzbereiten Kanäle."""
        return [n.name for n in self._notifiers]

    def attach(self, bus: EventBus) -> None:
        """Abonniert die relevanten Ereignisse auf dem Event-Bus."""
        bus.subscribe(EventType.POSITION_OPENED, self._on_position_opened)
        bus.subscribe(EventType.TRADE_CLOSED, self._on_trade_closed)
        bus.subscribe(EventType.ERROR, self._on_error)
        bus.subscribe(EventType.RISK_LIMIT_HIT, self._on_risk_limit)

    async def notify(self, event: NotificationEvent, title: str, message: str) -> None:
        """Versendet eine Nachricht über alle aktiven Kanäle.

        Deaktivierte Ereignisse werden übersprungen; Kanal-Fehler werden
        geloggt und unterdrückt.
        """
        if not self._is_event_enabled(event):
            return
        for notifier in self._notifiers:
            try:
                await notifier.send(title, message)
            except NotificationError as exc:
                logger.error("Kanal '%s': %s", notifier.name, exc)
            except Exception:
                logger.exception("Unerwarteter Fehler im Kanal '%s'", notifier.name)

    def _is_event_enabled(self, event: NotificationEvent) -> bool:
        events = self._config.events
        mapping = {
            NotificationEvent.TRADE_OPENED: events.trade_opened,
            NotificationEvent.TRADE_CLOSED: events.trade_closed,
            NotificationEvent.ERROR: events.error,
            NotificationEvent.MAX_DRAWDOWN: events.max_drawdown,
            NotificationEvent.DAILY_LOSS: events.daily_loss,
        }
        return mapping.get(event, True)

    # ------------------------------------------------------------------
    # Event-Handler
    # ------------------------------------------------------------------

    async def _on_position_opened(self, position: Position) -> None:
        await self.notify(
            NotificationEvent.TRADE_OPENED,
            f"📈 Position eröffnet: {position.symbol}",
            (
                f"Richtung: {position.side.value}\n"
                f"Menge: {position.amount:.8f}\n"
                f"Einstieg: {position.entry_price:.8f}\n"
                f"Stop-Loss: {position.stop_loss}\n"
                f"Take-Profit: {position.take_profit}\n"
                f"Strategie: {position.strategy}"
            ),
        )

    async def _on_trade_closed(self, trade: Trade) -> None:
        emoji = "✅" if trade.is_win else "❌"
        await self.notify(
            NotificationEvent.TRADE_CLOSED,
            f"{emoji} Trade geschlossen: {trade.symbol}",
            (
                f"Richtung: {trade.side.value}\n"
                f"Menge: {trade.amount:.8f}\n"
                f"Einstieg: {trade.entry_price:.8f}\n"
                f"Ausstieg: {trade.exit_price:.8f}\n"
                f"PnL: {trade.pnl:+.4f} ({trade.pnl_pct:+.2%})\n"
                f"Gebühren: {trade.fees:.4f}\n"
                f"Grund: {trade.exit_reason}\n"
                f"Strategie: {trade.strategy}"
            ),
        )

    async def _on_error(self, payload: object) -> None:
        await self.notify(
            NotificationEvent.ERROR,
            "⚠️ TradingBot-Fehler",
            str(payload),
        )

    async def _on_risk_limit(self, payload: object) -> None:
        text = str(payload)
        event = (
            NotificationEvent.MAX_DRAWDOWN if "Drawdown" in text else NotificationEvent.DAILY_LOSS
        )
        await self.notify(event, "🛑 Risiko-Limit erreicht – Handel gestoppt", text)
