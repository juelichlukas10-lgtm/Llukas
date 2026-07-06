"""Zentrale Enumerationen des Trading-Bots.

Alle Module verwenden diese Enums, damit Zustände und Typen systemweit
einheitlich und typsicher sind.
"""

from __future__ import annotations

from enum import StrEnum


class TradingMode(StrEnum):
    """Betriebsmodus des Bots."""

    PAPER = "paper"
    LIVE = "live"
    BACKTEST = "backtest"


class MarketType(StrEnum):
    """Markttyp einer Börse."""

    SPOT = "spot"
    FUTURES = "futures"


class OrderSide(StrEnum):
    """Kauf- oder Verkaufsseite einer Order."""

    BUY = "buy"
    SELL = "sell"

    @property
    def opposite(self) -> "OrderSide":
        """Gibt die Gegenseite zurück (buy <-> sell)."""
        return OrderSide.SELL if self is OrderSide.BUY else OrderSide.BUY


class OrderType(StrEnum):
    """Unterstützte Ordertypen."""

    MARKET = "market"
    LIMIT = "limit"
    STOP_MARKET = "stop_market"
    STOP_LIMIT = "stop_limit"
    TRAILING_STOP = "trailing_stop"


class OrderStatus(StrEnum):
    """Lebenszyklus-Status einer Order."""

    NEW = "new"
    OPEN = "open"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"
    EXPIRED = "expired"

    @property
    def is_terminal(self) -> bool:
        """True, wenn die Order keinen weiteren Zustandswechsel mehr erfährt."""
        return self in (
            OrderStatus.FILLED,
            OrderStatus.CANCELED,
            OrderStatus.REJECTED,
            OrderStatus.EXPIRED,
        )


class PositionSide(StrEnum):
    """Richtung einer offenen Position."""

    LONG = "long"
    SHORT = "short"

    @property
    def close_side(self) -> OrderSide:
        """Orderseite, mit der diese Position geschlossen wird."""
        return OrderSide.SELL if self is PositionSide.LONG else OrderSide.BUY

    @property
    def open_side(self) -> OrderSide:
        """Orderseite, mit der diese Position eröffnet wird."""
        return OrderSide.BUY if self is PositionSide.LONG else OrderSide.SELL


class SignalAction(StrEnum):
    """Handlungsanweisung einer Strategie."""

    BUY = "buy"
    SELL = "sell"
    CLOSE_LONG = "close_long"
    CLOSE_SHORT = "close_short"
    HOLD = "hold"


class Timeframe(StrEnum):
    """Unterstützte Kerzen-Timeframes."""

    M1 = "1m"
    M3 = "3m"
    M5 = "5m"
    M15 = "15m"
    M30 = "30m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"

    @property
    def seconds(self) -> int:
        """Dauer des Timeframes in Sekunden."""
        return _TIMEFRAME_SECONDS[self]

    @property
    def milliseconds(self) -> int:
        """Dauer des Timeframes in Millisekunden."""
        return self.seconds * 1000

    @property
    def pandas_freq(self) -> str:
        """Pandas-Resampling-Frequenz (z. B. '5min', '4h')."""
        return _PANDAS_FREQ[self]

    @classmethod
    def from_string(cls, value: str) -> "Timeframe":
        """Parst einen Timeframe-String; wirft ValueError bei unbekanntem Wert."""
        try:
            return cls(value)
        except ValueError as exc:
            valid = ", ".join(tf.value for tf in cls)
            raise ValueError(f"Unbekannter Timeframe '{value}'. Gültig: {valid}") from exc


_TIMEFRAME_SECONDS: dict[Timeframe, int] = {
    Timeframe.M1: 60,
    Timeframe.M3: 180,
    Timeframe.M5: 300,
    Timeframe.M15: 900,
    Timeframe.M30: 1800,
    Timeframe.H1: 3600,
    Timeframe.H4: 14400,
    Timeframe.D1: 86400,
}

_PANDAS_FREQ: dict[Timeframe, str] = {
    Timeframe.M1: "1min",
    Timeframe.M3: "3min",
    Timeframe.M5: "5min",
    Timeframe.M15: "15min",
    Timeframe.M30: "30min",
    Timeframe.H1: "1h",
    Timeframe.H4: "4h",
    Timeframe.D1: "1D",
}


class SizingMethod(StrEnum):
    """Verfahren zur Bestimmung der Positionsgröße."""

    FIXED = "fixed"
    PERCENT_RISK = "percent_risk"
    KELLY = "kelly"
    ATR = "atr"


class NotificationEvent(StrEnum):
    """Ereignisse, über die Benachrichtigungen versendet werden können."""

    TRADE_OPENED = "trade_opened"
    TRADE_CLOSED = "trade_closed"
    ERROR = "error"
    MAX_DRAWDOWN = "max_drawdown"
    DAILY_LOSS = "daily_loss"
