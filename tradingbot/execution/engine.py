"""Execution-Engine: setzt freigegebene Signale in Positionen um.

Verantwortlichkeiten:
    * Einstiegssignale in Orders übersetzen (nach Risiko-Freigabe und Sizing)
    * Offene Positionen verwalten (inkl. Teilverkäufe)
    * Preisgetriebene Exits ausführen (SL/TP/Trailing via RiskManager)
    * Trades bilanzieren, persistieren und auf dem Event-Bus publizieren
"""

from __future__ import annotations

from typing import Callable

from tradingbot.core.enums import OrderSide, PositionSide, SignalAction
from tradingbot.core.events import EventBus, EventType
from tradingbot.core.exceptions import OrderError
from tradingbot.core.logging import get_logger
from tradingbot.core.models import Order, Position, Signal, Trade, utc_now
from tradingbot.database.repository import Database
from tradingbot.execution.order_manager import OrderManager
from tradingbot.risk.manager import RiskDecision, RiskManager

logger = get_logger(__name__)


class ExecutionEngine:
    """Übersetzt Signale in Orders und verwaltet Positionen.

    Args:
        order_manager: Order-Verwaltung.
        risk_manager: Risiko-Regeln (Freigaben und Exit-Überwachung).
        event_bus: Bus für Positions-/Trade-Ereignisse.
        database: Optionale Persistenz für Positionen und Trades.
        equity_provider: Callable, das das aktuelle Gesamtkapital liefert
            (für Statistik-Zwecke in Trade-Events).
    """

    def __init__(
        self,
        order_manager: OrderManager,
        risk_manager: RiskManager,
        event_bus: EventBus,
        database: Database | None = None,
        equity_provider: Callable[[], float] | None = None,
    ) -> None:
        self._orders = order_manager
        self._risk = risk_manager
        self._bus = event_bus
        self._db = database
        self._equity_provider = equity_provider
        self._positions: dict[str, Position] = {}
        self._trades: list[Trade] = []

    # ------------------------------------------------------------------
    # Zugriff
    # ------------------------------------------------------------------

    @property
    def positions(self) -> dict[str, Position]:
        """Offene Positionen je Symbol (Kopie)."""
        return dict(self._positions)

    @property
    def closed_trades(self) -> list[Trade]:
        """Alle in dieser Session geschlossenen Trades (Kopie)."""
        return list(self._trades)

    def position_side(self, symbol: str) -> PositionSide | None:
        """Aktuelle Positionsrichtung für ein Symbol (None = flach)."""
        position = self._positions.get(symbol)
        return position.side if position else None

    def open_position_count(self) -> int:
        """Anzahl offener Positionen."""
        return len(self._positions)

    # ------------------------------------------------------------------
    # Signal-Verarbeitung
    # ------------------------------------------------------------------

    async def execute_entry(
        self,
        signal: Signal,
        amount: float,
        decision: RiskDecision,
        leverage: float = 1.0,
    ) -> Position | None:
        """Eröffnet eine Position gemäß freigegebenem Signal.

        Args:
            signal: Einstiegssignal (BUY oder SELL).
            amount: Positionsgröße in Basis-Einheiten.
            decision: Risiko-Freigabe mit effektiven Stops.
            leverage: Zu verwendender Hebel.

        Returns:
            Die eröffnete Position oder None bei Fehlschlag.
        """
        if not signal.is_entry:
            raise OrderError(f"execute_entry mit Nicht-Einstiegssignal aufgerufen: {signal.action}")
        if signal.symbol in self._positions:
            logger.warning("Position für %s existiert bereits – Einstieg übersprungen", signal.symbol)
            return None
        if amount <= 0:
            logger.warning("Positionsgröße %.8f für %s nicht positiv – übersprungen", amount, signal.symbol)
            return None

        side = OrderSide.BUY if signal.action is SignalAction.BUY else OrderSide.SELL
        try:
            order = await self._orders.submit_market(
                signal.symbol, side, amount, strategy=signal.strategy
            )
        except Exception:
            logger.exception("Einstiegs-Order für %s fehlgeschlagen", signal.symbol)
            await self._bus.publish(EventType.ERROR, f"Einstiegs-Order {signal.symbol} fehlgeschlagen")
            return None

        if order.filled <= 0:
            logger.warning("Einstiegs-Order %s wurde nicht gefüllt", order.id[:8])
            return None

        position = Position(
            symbol=signal.symbol,
            side=PositionSide.LONG if side is OrderSide.BUY else PositionSide.SHORT,
            amount=order.filled,
            entry_price=order.average_price,
            leverage=leverage,
            stop_loss=decision.stop_loss,
            take_profit=decision.take_profit,
            trailing_stop=decision.trailing_stop,
            strategy=signal.strategy,
            fees_paid=order.fee,
        )
        self._positions[signal.symbol] = position
        if self._db is not None:
            self._db.save_position(position)
        await self._bus.publish(EventType.POSITION_OPENED, position)
        logger.info(
            "Position eröffnet: %s %s %.8f @ %.8f (SL=%s, TP=%s, Strategie=%s)",
            position.side.value,
            position.symbol,
            position.amount,
            position.entry_price,
            position.stop_loss,
            position.take_profit,
            position.strategy,
        )
        return position

    async def close_position(
        self, symbol: str, reason: str = "signal", portion: float = 1.0
    ) -> Trade | None:
        """Schließt eine Position ganz oder teilweise (Teilverkauf).

        Args:
            symbol: Symbol der Position.
            reason: Exit-Grund (``signal``, ``stop_loss``, ``take_profit``, ...).
            portion: Anteil der Position in (0, 1]; 1.0 = vollständig.

        Returns:
            Der realisierte Trade oder None, wenn keine Position existiert
            oder die Order fehlschlägt.
        """
        position = self._positions.get(symbol)
        if position is None:
            logger.debug("close_position: keine offene Position für %s", symbol)
            return None
        if not 0 < portion <= 1.0:
            raise OrderError(f"Ungültiger Teilverkaufs-Anteil: {portion}")

        close_amount = position.amount * portion
        try:
            order = await self._orders.submit_market(
                symbol,
                position.side.close_side,
                close_amount,
                strategy=position.strategy,
                reduce_only=True,
            )
        except Exception:
            logger.exception("Exit-Order für %s fehlgeschlagen", symbol)
            await self._bus.publish(EventType.ERROR, f"Exit-Order {symbol} fehlgeschlagen")
            return None

        if order.filled <= 0:
            logger.warning("Exit-Order %s wurde nicht gefüllt", order.id[:8])
            return None

        trade = self._settle_close(position, order, reason)
        return trade

    def _settle_close(self, position: Position, order: Order, reason: str) -> Trade:
        """Bilanziert einen (Teil-)Exit und aktualisiert den Bestand."""
        exit_price = order.average_price
        amount = order.filled

        if position.side is PositionSide.LONG:
            gross_pnl = (exit_price - position.entry_price) * amount
        else:
            gross_pnl = (position.entry_price - exit_price) * amount
        # Anteilige Einstiegsgebühr + Exit-Gebühr.
        entry_fee_share = position.fees_paid * (amount / max(position.amount, 1e-12))
        total_fees = entry_fee_share + order.fee
        net_pnl = gross_pnl - total_fees

        trade = Trade(
            symbol=position.symbol,
            side=position.side,
            amount=amount,
            entry_price=position.entry_price,
            exit_price=exit_price,
            pnl=net_pnl,
            fees=total_fees,
            strategy=position.strategy,
            opened_at=position.opened_at,
            closed_at=utc_now(),
            exit_reason=reason,
            leverage=position.leverage,
        )
        self._trades.append(trade)
        self._risk.record_trade(trade)

        remaining = position.amount - amount
        if remaining <= 1e-12:
            self._positions.pop(position.symbol, None)
            if self._db is not None:
                self._db.delete_position(position.id)
        else:
            position.amount = remaining
            position.fees_paid -= entry_fee_share
            position.realized_pnl += net_pnl
            if self._db is not None:
                self._db.save_position(position)

        if self._db is not None:
            self._db.save_trade(trade)

        logger.info(
            "Trade geschlossen: %s %s %.8f | Entry %.8f -> Exit %.8f | PnL %.4f (%s)",
            trade.side.value,
            trade.symbol,
            trade.amount,
            trade.entry_price,
            trade.exit_price,
            trade.pnl,
            reason,
        )
        return trade

    # ------------------------------------------------------------------
    # Preisgetriebene Überwachung
    # ------------------------------------------------------------------

    async def on_price_update(self, symbol: str, price: float) -> Trade | None:
        """Prüft SL/TP/Trailing der Position eines Symbols bei neuem Preis.

        Args:
            symbol: Symbol des Preisupdates.
            price: Aktueller Marktpreis.

        Returns:
            Trade, falls ein Exit ausgelöst wurde, sonst None.
        """
        position = self._positions.get(symbol)
        if position is None:
            return None
        exit_reason = self._risk.check_exit(position, price)
        if exit_reason is None:
            return None
        logger.info("Risiko-Exit für %s ausgelöst: %s bei %.8f", symbol, exit_reason, price)
        trade = await self.close_position(symbol, reason=exit_reason)
        if trade is not None:
            await self._publish_trade_events(trade)
        return trade

    async def close_all(self, reason: str = "shutdown") -> list[Trade]:
        """Schließt alle offenen Positionen (z. B. bei Notaus/Shutdown)."""
        trades: list[Trade] = []
        for symbol in list(self._positions):
            trade = await self.close_position(symbol, reason=reason)
            if trade is not None:
                trades.append(trade)
                await self._publish_trade_events(trade)
        return trades

    async def publish_trade(self, trade: Trade) -> None:
        """Publiziert Trade-Ereignisse (für signalbasierte Exits von außen)."""
        await self._publish_trade_events(trade)

    async def _publish_trade_events(self, trade: Trade) -> None:
        await self._bus.publish(EventType.TRADE_CLOSED, trade)
        if trade.symbol not in self._positions:
            await self._bus.publish(EventType.POSITION_CLOSED, trade)
