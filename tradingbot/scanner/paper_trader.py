"""Paper-Trading-Depot des Buy-the-Dip-Scanners.

Eigenständiges, long-only Paper-Depot — vollständig getrennt vom
Trading-Bot (eigenes Kapital, eigene Datenbanktabellen). Handelt
ausschließlich auf Basis der vom Scanner erkannten Setups:

    * **Einstieg**: sobald ein Setup den Status ``ENTRY`` erreicht und
      noch keine Position im Symbol besteht (und Kapital/Positionslimit
      es zulassen). Größe wird risikobasiert aus der Stop-Distanz
      berechnet (analog zum Bot-Sizing).
    * **Teilverkauf**: bei Erreichen von Ziel 1 wird die Hälfte verkauft
      und der Stop auf den Einstand gezogen (konfigurierbar).
    * **Exit**: Restposition bei Ziel 2 oder Stop-Loss; sofortiger
      Voll-Exit, wenn das Setup als ``invalidated`` markiert wird.

Alle Zustandsänderungen werden sofort in der Datenbank persistiert,
damit das Dashboard sie unabhängig vom Scanner-Prozess anzeigen kann.
"""

from __future__ import annotations

import pandas as pd

from tradingbot.core.config import ScannerPaperTradingConfig
from tradingbot.core.logging import get_logger
from tradingbot.core.models import utc_now
from tradingbot.database.repository import Database
from tradingbot.scanner.models import (
    DipSignal,
    ScannerPortfolioSnapshot,
    ScannerPosition,
    ScannerTrade,
    SetupStatus,
)

logger = get_logger(__name__)


class ScannerPaperTrader:
    """Verwaltet das Paper-Depot des Scanners.

    Args:
        config: Paper-Trading-Parameter.
        database: Persistenzschicht (gemeinsame DB mit den Scanner-Signalen).
    """

    def __init__(self, config: ScannerPaperTradingConfig, database: Database) -> None:
        self._config = config
        self._db = database
        self._cash = database.get_scanner_cash(default=config.initial_balance)
        self._positions: dict[str, ScannerPosition] = {
            record.symbol: ScannerPosition(
                symbol=record.symbol,
                name=record.name,
                amount=record.amount,
                entry_price=record.entry_price,
                stop_loss=record.stop_loss,
                target_1=record.target_1,
                target_2=record.target_2,
                opened_at=record.opened_at,
                partial_exit_done=bool(record.partial_exit_done),
                fees_paid=record.fees_paid,
                realized_pnl=record.realized_pnl,
            )
            for record in database.get_scanner_positions()
        }
        if self._positions:
            logger.info("Scanner-Depot geladen: %d offene Positionen", len(self._positions))

    # ------------------------------------------------------------------
    # Öffentliche API
    # ------------------------------------------------------------------

    @property
    def open_positions(self) -> dict[str, ScannerPosition]:
        """Kopie der aktuell offenen Positionen."""
        return dict(self._positions)

    def process_cycle(
        self,
        signals: dict[str, DipSignal],
        price_data: dict[str, pd.DataFrame],
        invalidated_symbols: set[str],
    ) -> tuple[list[ScannerPosition], list[ScannerTrade]]:
        """Verarbeitet einen Scan-Zyklus: Exits zuerst, dann neue Einstiege.

        Args:
            signals: Aktuell erkannte Setups dieses Zyklus (Symbol -> Signal).
            price_data: OHLCV-Daten aller gescannten Symbole (für Kursstand
                offener Positionen, auch wenn sie kein aktives Setup mehr sind).
            invalidated_symbols: Symbole, deren Setup in diesem Zyklus als
                ungültig markiert wurde (löst Sofort-Exit aus).

        Returns:
            Tuple aus (neu eröffnete Positionen, abgeschlossene [Teil-]Trades)
            – für Benachrichtigungen der aufrufenden Scan-Engine.
        """
        if not self._config.enabled:
            return [], []

        closed_trades = self._process_exits(price_data, invalidated_symbols)
        opened_positions = self._process_entries(signals)
        return opened_positions, closed_trades

    def snapshot(self) -> ScannerPortfolioSnapshot:
        """Aktueller Depot-Status für das Dashboard."""
        trades = self._db.get_scanner_trades(limit=None)
        wins = sum(1 for t in trades if t.pnl > 0)
        equity = self._cash + sum(
            pos.amount * pos.entry_price for pos in self._positions.values()
        )
        return ScannerPortfolioSnapshot(
            cash=self._cash,
            equity=equity,
            open_positions=len(self._positions),
            total_trades=len(trades),
            win_rate=wins / len(trades) if trades else 0.0,
            total_pnl=sum(t.pnl for t in trades),
        )

    # ------------------------------------------------------------------
    # Exits
    # ------------------------------------------------------------------

    def _process_exits(
        self, price_data: dict[str, pd.DataFrame], invalidated_symbols: set[str]
    ) -> list[ScannerTrade]:
        closed: list[ScannerTrade] = []
        for symbol in list(self._positions):
            df = price_data.get(symbol)
            if df is None or df.empty:
                continue  # keine aktuellen Kursdaten -> Position unangetastet lassen
            price = float(df["close"].iloc[-1])
            position = self._positions[symbol]

            if symbol in invalidated_symbols:
                closed.append(self._close_position(position, price, "invalidated"))
                continue

            if price <= position.stop_loss:
                closed.append(self._close_position(position, position.stop_loss, "stop_loss"))
                continue

            if not position.partial_exit_done:
                if price < position.target_1:
                    continue
                if self._config.partial_exit_at_target1:
                    closed.append(self._partial_close(position, position.target_1))
                else:
                    closed.append(self._close_position(position, position.target_1, "target_1"))
            elif price >= position.target_2:
                closed.append(self._close_position(position, position.target_2, "target_2"))
        return closed

    def _partial_close(self, position: ScannerPosition, price: float) -> ScannerTrade:
        """Verkauft die Hälfte der Position, zieht den Stop auf den Einstand."""
        amount = position.amount / 2.0
        proceeds = amount * price
        fee = proceeds * self._config.commission_rate
        pnl = (price - position.entry_price) * amount - fee

        self._cash += proceeds - fee
        position.amount -= amount
        position.partial_exit_done = True
        position.stop_loss = max(position.stop_loss, position.entry_price)
        position.realized_pnl += pnl

        self._db.save_scanner_position(position)
        self._db.save_scanner_cash(self._cash)

        trade = ScannerTrade(
            symbol=position.symbol, amount=amount, entry_price=position.entry_price,
            exit_price=price, pnl=pnl, fees=fee, exit_reason="target_1",
            opened_at=position.opened_at,
        )
        self._db.save_scanner_trade(trade)
        logger.info(
            "Scanner-Teilverkauf: %s %.2f Stk @ %.2f (Ziel 1), PnL %.2f, Stop -> Einstand",
            position.symbol, amount, price, pnl,
        )
        return trade

    def _close_position(self, position: ScannerPosition, price: float, reason: str) -> ScannerTrade:
        """Schließt eine (Rest-)Position vollständig."""
        proceeds = position.amount * price
        fee = proceeds * self._config.commission_rate
        pnl = (price - position.entry_price) * position.amount - fee

        self._cash += proceeds - fee
        self._db.delete_scanner_position(position.symbol)
        self._db.save_scanner_cash(self._cash)
        self._positions.pop(position.symbol, None)

        trade = ScannerTrade(
            symbol=position.symbol, amount=position.amount, entry_price=position.entry_price,
            exit_price=price, pnl=pnl, fees=fee, exit_reason=reason,
            opened_at=position.opened_at,
        )
        self._db.save_scanner_trade(trade)
        logger.info(
            "Scanner-Position geschlossen: %s %.2f Stk @ %.2f (%s), PnL %.2f",
            position.symbol, position.amount, price, reason, pnl,
        )
        return trade

    # ------------------------------------------------------------------
    # Einstiege
    # ------------------------------------------------------------------

    def _process_entries(self, signals: dict[str, DipSignal]) -> list[ScannerPosition]:
        if len(self._positions) >= self._config.max_open_positions:
            return []

        candidates = [
            s for s in signals.values()
            if s.status is SetupStatus.ENTRY and s.symbol not in self._positions
        ]
        candidates.sort(key=lambda s: s.score, reverse=True)

        opened: list[ScannerPosition] = []
        for signal in candidates:
            if len(self._positions) >= self._config.max_open_positions:
                break
            position = self._open_position(signal)
            if position is not None:
                opened.append(position)
        return opened

    def _open_position(self, signal: DipSignal) -> ScannerPosition | None:
        """Eröffnet eine risikobasiert bemessene Position."""
        risk_amount = self._equity_estimate() * self._config.risk_per_trade
        stop_distance = signal.entry_price - signal.stop_loss
        if stop_distance <= 0:
            logger.warning("Scanner: ungültige Stop-Distanz für %s – Einstieg übersprungen", signal.symbol)
            return None

        amount = risk_amount / stop_distance
        cost = amount * signal.entry_price
        fee = cost * self._config.commission_rate
        total = cost + fee

        if total > self._cash:
            scale = self._cash / total * 0.99 if total > 0 else 0.0
            amount *= scale
            cost = amount * signal.entry_price
            fee = cost * self._config.commission_rate
            total = cost + fee
        if amount <= 0 or total > self._cash:
            logger.debug("Scanner: unzureichendes Kapital für %s – Einstieg übersprungen", signal.symbol)
            return None

        self._cash -= total
        position = ScannerPosition(
            symbol=signal.symbol,
            name=signal.name,
            amount=amount,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            target_1=signal.target_1,
            target_2=signal.target_2,
            fees_paid=fee,
        )
        self._positions[signal.symbol] = position
        self._db.save_scanner_position(position)
        self._db.save_scanner_cash(self._cash)
        logger.info(
            "Scanner-Paper-Kauf: %s %.2f Stk @ %.2f (Score %.0f, Stop %.2f, Ziel1 %.2f, Ziel2 %.2f)",
            signal.symbol, amount, signal.entry_price, signal.score,
            signal.stop_loss, signal.target_1, signal.target_2,
        )
        return position

    def _equity_estimate(self) -> float:
        """Grobe Equity-Schätzung (Cash + Buchwert offener Positionen) fürs Sizing."""
        return self._cash + sum(p.amount * p.entry_price for p in self._positions.values())
