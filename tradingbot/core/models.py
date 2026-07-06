"""Domänenmodelle des Trading-Bots.

Leichtgewichtige, unveränderliche bzw. kontrolliert veränderliche
Datenklassen, die zwischen allen Modulen (Daten, Strategie, Risiko,
Execution, Persistenz) ausgetauscht werden.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from tradingbot.core.enums import (
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
    SignalAction,
    Timeframe,
)


def utc_now() -> datetime:
    """Aktuelle Zeit als timezone-aware UTC-Datetime."""
    return datetime.now(timezone.utc)


def new_id() -> str:
    """Erzeugt eine eindeutige interne ID (UUID4-Hex)."""
    return uuid.uuid4().hex


@dataclass(frozen=True, slots=True)
class Candle:
    """Eine OHLCV-Kerze.

    Attributes:
        symbol: Handelspaar, z. B. ``"BTC/USDT"``.
        timeframe: Timeframe der Kerze.
        timestamp: Eröffnungszeitpunkt (UTC).
        open: Eröffnungskurs.
        high: Höchstkurs.
        low: Tiefstkurs.
        close: Schlusskurs.
        volume: Basis-Volumen der Kerze.
    """

    symbol: str
    timeframe: Timeframe
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def is_bullish(self) -> bool:
        """True, wenn die Kerze grün ist (close > open)."""
        return self.close > self.open

    @property
    def range(self) -> float:
        """Spanne zwischen Hoch und Tief."""
        return self.high - self.low


@dataclass(frozen=True, slots=True)
class Ticker:
    """Aktueller Ticker eines Symbols."""

    symbol: str
    timestamp: datetime
    bid: float
    ask: float
    last: float
    volume_24h: float = 0.0

    @property
    def mid(self) -> float:
        """Mittelkurs zwischen Bid und Ask."""
        return (self.bid + self.ask) / 2.0

    @property
    def spread(self) -> float:
        """Absoluter Spread zwischen Ask und Bid."""
        return self.ask - self.bid


@dataclass(frozen=True, slots=True)
class OrderBookLevel:
    """Einzelnes Preisniveau im Orderbuch."""

    price: float
    amount: float


@dataclass(frozen=True, slots=True)
class OrderBook:
    """Snapshot eines Orderbuchs (Bids absteigend, Asks aufsteigend sortiert)."""

    symbol: str
    timestamp: datetime
    bids: tuple[OrderBookLevel, ...]
    asks: tuple[OrderBookLevel, ...]

    @property
    def best_bid(self) -> float | None:
        """Bester Geldkurs oder None bei leerem Buch."""
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> float | None:
        """Bester Briefkurs oder None bei leerem Buch."""
        return self.asks[0].price if self.asks else None

    def imbalance(self, depth: int = 10) -> float:
        """Bid/Ask-Volumen-Ungleichgewicht in ``[-1, 1]``.

        Positiv = Kaufdruck, negativ = Verkaufsdruck.

        Args:
            depth: Anzahl der berücksichtigten Levels je Seite.
        """
        bid_vol = sum(level.amount for level in self.bids[:depth])
        ask_vol = sum(level.amount for level in self.asks[:depth])
        total = bid_vol + ask_vol
        if total <= 0:
            return 0.0
        return (bid_vol - ask_vol) / total


@dataclass(frozen=True, slots=True)
class TradeTick:
    """Einzelner öffentlicher Markt-Trade (Time & Sales)."""

    symbol: str
    timestamp: datetime
    price: float
    amount: float
    side: OrderSide


@dataclass(frozen=True, slots=True)
class FundingRate:
    """Funding-Rate eines Perpetual-Futures."""

    symbol: str
    timestamp: datetime
    rate: float
    next_funding_time: datetime | None = None


@dataclass(frozen=True, slots=True)
class OpenInterest:
    """Open Interest eines Derivats."""

    symbol: str
    timestamp: datetime
    open_interest: float
    open_interest_value: float | None = None


@dataclass(frozen=True, slots=True)
class Balance:
    """Guthaben einer einzelnen Währung."""

    currency: str
    free: float
    used: float

    @property
    def total(self) -> float:
        """Gesamtguthaben (frei + gebunden)."""
        return self.free + self.used


@dataclass(frozen=True, slots=True)
class Signal:
    """Handelssignal einer Strategie.

    Strategien liefern ausschließlich Signale; Risiko- und
    Execution-Schicht entscheiden über die tatsächliche Umsetzung.

    Attributes:
        action: Gewünschte Aktion (buy/sell/close/hold).
        symbol: Handelspaar.
        strategy: Registry-Name der auslösenden Strategie.
        timestamp: Signalzeitpunkt.
        price: Referenzpreis zum Signalzeitpunkt.
        confidence: Signalstärke in ``[0, 1]``.
        stop_loss: Optionaler strategie-spezifischer Stop-Loss-Preis.
        take_profit: Optionaler strategie-spezifischer Take-Profit-Preis.
        size_hint: Optionaler Vorschlag für die Positionsgröße (Basis-Einheiten).
        reason: Menschlich lesbare Begründung (für Logs/Datenbank).
        metadata: Beliebige Zusatzinformationen der Strategie.
    """

    action: SignalAction
    symbol: str
    strategy: str
    timestamp: datetime
    price: float
    confidence: float = 1.0
    stop_loss: float | None = None
    take_profit: float | None = None
    size_hint: float | None = None
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_entry(self) -> bool:
        """True bei Einstiegssignal (buy oder sell)."""
        return self.action in (SignalAction.BUY, SignalAction.SELL)

    @property
    def is_exit(self) -> bool:
        """True bei Ausstiegssignal (close_long oder close_short)."""
        return self.action in (SignalAction.CLOSE_LONG, SignalAction.CLOSE_SHORT)


@dataclass(slots=True)
class Order:
    """Order mit vollständigem Lebenszyklus.

    Attributes:
        id: Interne, eindeutige Order-ID.
        exchange_id: ID der Börse (None solange nicht platziert).
        symbol: Handelspaar.
        side: Kauf oder Verkauf.
        type: Ordertyp.
        amount: Bestellte Menge in Basis-Einheiten.
        price: Limitpreis (nur Limit-/Stop-Limit-Orders).
        stop_price: Auslösepreis für Stop-Orders.
        trailing_delta: Trailing-Abstand als Bruchteil (z. B. 0.01 = 1 %).
        status: Aktueller Orderstatus.
        filled: Bereits ausgeführte Menge.
        average_price: Durchschnittlicher Ausführungspreis.
        fee: Angefallene Gebühren in Quote-Währung.
        reduce_only: Order darf Position nur verkleinern.
        post_only: Order darf nur als Maker ausgeführt werden.
        strategy: Verursachende Strategie.
        created_at: Erstellungszeitpunkt.
        updated_at: Zeitpunkt der letzten Statusänderung.
    """

    symbol: str
    side: OrderSide
    type: OrderType
    amount: float
    id: str = field(default_factory=new_id)
    exchange_id: str | None = None
    price: float | None = None
    stop_price: float | None = None
    trailing_delta: float | None = None
    status: OrderStatus = OrderStatus.NEW
    filled: float = 0.0
    average_price: float = 0.0
    fee: float = 0.0
    reduce_only: bool = False
    post_only: bool = False
    strategy: str = ""
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    @property
    def remaining(self) -> float:
        """Noch offene Menge."""
        return max(self.amount - self.filled, 0.0)

    @property
    def is_open(self) -> bool:
        """True, wenn die Order noch aktiv ist."""
        return not self.status.is_terminal

    def record_fill(self, amount: float, price: float, fee: float = 0.0) -> None:
        """Verbucht eine (Teil-)Ausführung und aktualisiert den Status.

        Args:
            amount: Ausgeführte Menge dieser Teilausführung.
            price: Ausführungspreis dieser Teilausführung.
            fee: Gebühr dieser Teilausführung in Quote-Währung.

        Raises:
            ValueError: Wenn ``amount`` nicht positiv ist oder die Order
                überfüllt würde.
        """
        if amount <= 0:
            raise ValueError("Fill-Menge muss positiv sein")
        if amount > self.remaining + 1e-12:
            raise ValueError(
                f"Fill von {amount} überschreitet offene Menge {self.remaining} (Order {self.id})"
            )
        total_cost = self.average_price * self.filled + price * amount
        self.filled = min(self.filled + amount, self.amount)
        self.average_price = total_cost / self.filled if self.filled > 0 else 0.0
        self.fee += fee
        self.status = (
            OrderStatus.FILLED if self.remaining <= 1e-12 else OrderStatus.PARTIALLY_FILLED
        )
        self.updated_at = utc_now()


@dataclass(slots=True)
class Position:
    """Offene Handelsposition.

    Attributes:
        id: Interne Positions-ID.
        symbol: Handelspaar.
        side: Long oder Short.
        amount: Positionsgröße in Basis-Einheiten.
        entry_price: Durchschnittlicher Einstiegspreis.
        leverage: Verwendeter Hebel.
        stop_loss: Aktueller Stop-Loss-Preis (None = keiner).
        take_profit: Aktueller Take-Profit-Preis (None = keiner).
        trailing_stop: Trailing-Abstand als Bruchteil (None = deaktiviert).
        break_even_done: True, wenn der SL bereits auf Einstand gezogen wurde.
        highest_price: Höchster seit Eröffnung gesehener Preis (für Trailing).
        lowest_price: Tiefster seit Eröffnung gesehener Preis (für Trailing).
        strategy: Eröffnende Strategie.
        opened_at: Eröffnungszeitpunkt.
        fees_paid: Kumulierte Gebühren in Quote-Währung.
        realized_pnl: Bereits realisierter PnL aus Teilverkäufen.
    """

    symbol: str
    side: PositionSide
    amount: float
    entry_price: float
    id: str = field(default_factory=new_id)
    leverage: float = 1.0
    stop_loss: float | None = None
    take_profit: float | None = None
    trailing_stop: float | None = None
    break_even_done: bool = False
    highest_price: float = 0.0
    lowest_price: float = 0.0
    strategy: str = ""
    opened_at: datetime = field(default_factory=utc_now)
    fees_paid: float = 0.0
    realized_pnl: float = 0.0

    def __post_init__(self) -> None:
        if self.highest_price <= 0:
            self.highest_price = self.entry_price
        if self.lowest_price <= 0:
            self.lowest_price = self.entry_price

    @property
    def notional(self) -> float:
        """Positionswert zum Einstiegspreis in Quote-Währung."""
        return self.amount * self.entry_price

    def unrealized_pnl(self, current_price: float) -> float:
        """Unrealisierter Gewinn/Verlust zum aktuellen Preis.

        Args:
            current_price: Aktueller Marktpreis.
        """
        if self.side is PositionSide.LONG:
            return (current_price - self.entry_price) * self.amount
        return (self.entry_price - current_price) * self.amount

    def unrealized_pnl_pct(self, current_price: float) -> float:
        """Unrealisierter PnL relativ zum eingesetzten Kapital (mit Hebel)."""
        if self.entry_price <= 0:
            return 0.0
        raw = (current_price - self.entry_price) / self.entry_price
        direction = 1.0 if self.side is PositionSide.LONG else -1.0
        return raw * direction * self.leverage

    def update_extremes(self, price: float) -> None:
        """Aktualisiert Hoch/Tief-Wasserstände (für Trailing-Stop-Logik)."""
        if price > self.highest_price:
            self.highest_price = price
        if price < self.lowest_price:
            self.lowest_price = price


@dataclass(slots=True)
class Trade:
    """Abgeschlossener (Round-Trip-)Trade für Auswertung und Persistenz.

    Attributes:
        id: Interne Trade-ID.
        symbol: Handelspaar.
        side: Richtung der ursprünglichen Position.
        amount: Gehandelte Menge in Basis-Einheiten.
        entry_price: Einstiegspreis.
        exit_price: Ausstiegspreis.
        pnl: Realisierter Gewinn/Verlust nach Gebühren (Quote-Währung).
        fees: Gesamte Gebühren.
        strategy: Verantwortliche Strategie.
        opened_at: Eröffnungszeitpunkt.
        closed_at: Schlusszeitpunkt.
        exit_reason: Grund des Ausstiegs (signal, stop_loss, take_profit, ...).
        leverage: Verwendeter Hebel.
    """

    symbol: str
    side: PositionSide
    amount: float
    entry_price: float
    exit_price: float
    pnl: float
    fees: float
    strategy: str
    opened_at: datetime
    closed_at: datetime
    exit_reason: str = "signal"
    leverage: float = 1.0
    id: str = field(default_factory=new_id)

    @property
    def pnl_pct(self) -> float:
        """PnL relativ zum eingesetzten Kapital (Notional/Hebel)."""
        margin = self.amount * self.entry_price / max(self.leverage, 1e-12)
        if margin <= 0:
            return 0.0
        return self.pnl / margin

    @property
    def is_win(self) -> bool:
        """True bei positivem PnL."""
        return self.pnl > 0

    @property
    def duration_seconds(self) -> float:
        """Haltedauer in Sekunden."""
        return (self.closed_at - self.opened_at).total_seconds()
