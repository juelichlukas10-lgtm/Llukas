"""Zentrales Risiko-Management.

Der :class:`RiskManager` ist die Instanz zwischen Strategie-Signalen und
Ausführung. Er erzwingt:

    * Max. offene Positionen und tägliches Trade-Limit
    * Max. Tagesverlust und max. Drawdown (Handelsstopp)
    * Cooldown nach Verlustserien
    * Stop-Loss / Take-Profit (Defaults, falls die Strategie keine setzt)
    * Trailing-Stop und Break-Even-Nachziehen offener Positionen
    * Hebel-Begrenzung
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from tradingbot.core.config import RiskConfig
from tradingbot.core.enums import PositionSide, SignalAction
from tradingbot.core.logging import get_logger
from tradingbot.core.models import Position, Signal, Trade, utc_now

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class RiskDecision:
    """Ergebnis einer Risiko-Prüfung für ein Einstiegssignal.

    Attributes:
        approved: True, wenn der Trade ausgeführt werden darf.
        reason: Begründung (insbesondere bei Ablehnung).
        stop_loss: Zu verwendender Stop-Loss-Preis (None = keiner).
        take_profit: Zu verwendender Take-Profit-Preis (None = keiner).
        trailing_stop: Trailing-Abstand als Bruchteil (None = deaktiviert).
    """

    approved: bool
    reason: str = ""
    stop_loss: float | None = None
    take_profit: float | None = None
    trailing_stop: float | None = None


@dataclass(slots=True)
class _DailyState:
    """Interner Tageszustand (wird bei Datumswechsel zurückgesetzt)."""

    day: date
    realized_pnl: float = 0.0
    trade_count: int = 0
    start_equity: float = 0.0


class RiskManager:
    """Erzwingt alle konfigurierten Risiko-Regeln.

    Args:
        config: Risiko-Konfiguration.
        initial_equity: Startkapital (Basis für Drawdown-Berechnung).
    """

    def __init__(self, config: RiskConfig, initial_equity: float) -> None:
        self._config = config
        self._peak_equity = initial_equity
        self._current_equity = initial_equity
        self._daily = _DailyState(day=utc_now().date(), start_equity=initial_equity)
        self._consecutive_losses = 0
        self._cooldown_until: datetime | None = None
        self._halt_reason: str | None = None

    # ------------------------------------------------------------------
    # Zustand & Kennzahlen
    # ------------------------------------------------------------------

    @property
    def current_drawdown(self) -> float:
        """Aktueller Drawdown vom Equity-Hoch als Bruchteil in [0, 1]."""
        if self._peak_equity <= 0:
            return 0.0
        return max(0.0, 1.0 - self._current_equity / self._peak_equity)

    @property
    def daily_loss_pct(self) -> float:
        """Realisierter Tagesverlust relativ zum Tagesstartkapital (>= 0)."""
        self._roll_day_if_needed()
        if self._daily.start_equity <= 0:
            return 0.0
        return max(0.0, -self._daily.realized_pnl / self._daily.start_equity)

    @property
    def is_halted(self) -> tuple[bool, str]:
        """(True, Grund) wenn der Handel derzeit gestoppt ist."""
        self._roll_day_if_needed()
        if self._halt_reason is not None:
            return True, self._halt_reason
        if self._cooldown_until is not None:
            if utc_now() < self._cooldown_until:
                return True, (
                    f"Cooldown nach {self._consecutive_losses} Verlusten in Folge "
                    f"bis {self._cooldown_until:%H:%M:%S} UTC"
                )
            self._cooldown_until = None
        return False, ""

    def update_equity(self, equity: float) -> str | None:
        """Aktualisiert das Kapital und prüft Drawdown-/Tagesverlust-Limits.

        Args:
            equity: Aktuelles Gesamtkapital (inkl. unrealisiertem PnL).

        Returns:
            Grund des Handelsstopps, falls ein Limit gerissen wurde, sonst None.
        """
        self._roll_day_if_needed()
        self._current_equity = equity
        self._peak_equity = max(self._peak_equity, equity)

        if self.current_drawdown >= self._config.max_drawdown:
            self._halt_reason = (
                f"Max. Drawdown erreicht: {self.current_drawdown:.1%} >= {self._config.max_drawdown:.1%}"
            )
            logger.error("HANDELSSTOPP: %s", self._halt_reason)
            return self._halt_reason

        if self.daily_loss_pct >= self._config.max_daily_loss:
            self._halt_reason = (
                f"Max. Tagesverlust erreicht: {self.daily_loss_pct:.1%} >= "
                f"{self._config.max_daily_loss:.1%}"
            )
            logger.error("HANDELSSTOPP: %s", self._halt_reason)
            return self._halt_reason
        return None

    def record_trade(self, trade: Trade) -> None:
        """Verbucht einen abgeschlossenen Trade (PnL, Streaks, Tageszähler)."""
        self._roll_day_if_needed()
        self._daily.realized_pnl += trade.pnl
        self._daily.trade_count += 1

        if trade.pnl < 0:
            self._consecutive_losses += 1
            cfg = self._config.loss_streak_cooldown
            if (
                self._consecutive_losses >= cfg.max_consecutive_losses
                and cfg.cooldown_minutes > 0
            ):
                self._cooldown_until = utc_now() + timedelta(minutes=cfg.cooldown_minutes)
                logger.warning(
                    "%d Verluste in Folge – Cooldown bis %s",
                    self._consecutive_losses,
                    self._cooldown_until,
                )
        else:
            self._consecutive_losses = 0

    def reset_halt(self) -> None:
        """Hebt einen Handelsstopp manuell auf (z. B. nach Prüfung)."""
        logger.warning("Handelsstopp manuell aufgehoben (war: %s)", self._halt_reason)
        self._halt_reason = None
        self._cooldown_until = None

    # ------------------------------------------------------------------
    # Einstiegs-Prüfung
    # ------------------------------------------------------------------

    def evaluate_entry(
        self,
        signal: Signal,
        open_positions: int,
    ) -> RiskDecision:
        """Prüft ein Einstiegssignal gegen alle Risiko-Regeln.

        Args:
            signal: Einstiegssignal (BUY oder SELL).
            open_positions: Anzahl aktuell offener Positionen.

        Returns:
            :class:`RiskDecision` mit Freigabe und effektiven Stops.
        """
        if not signal.is_entry:
            return RiskDecision(approved=False, reason="Kein Einstiegssignal")

        halted, halt_reason = self.is_halted
        if halted:
            return RiskDecision(approved=False, reason=halt_reason)

        if open_positions >= self._config.max_open_positions:
            return RiskDecision(
                approved=False,
                reason=f"Max. offene Positionen erreicht ({open_positions}/"
                f"{self._config.max_open_positions})",
            )

        self._roll_day_if_needed()
        if self._daily.trade_count >= self._config.max_daily_trades:
            return RiskDecision(
                approved=False,
                reason=f"Tägliches Trade-Limit erreicht ({self._daily.trade_count}/"
                f"{self._config.max_daily_trades})",
            )

        stop_loss, take_profit = self._effective_stops(signal)
        trailing = self._config.trailing_stop if self._config.trailing_stop > 0 else None
        return RiskDecision(
            approved=True,
            reason="OK",
            stop_loss=stop_loss,
            take_profit=take_profit,
            trailing_stop=trailing,
        )

    def _effective_stops(self, signal: Signal) -> tuple[float | None, float | None]:
        """Kombiniert Strategie-Stops mit den konfigurierten Defaults."""
        price = signal.price
        is_long = signal.action is SignalAction.BUY

        stop_loss = signal.stop_loss
        if stop_loss is None and self._config.stop_loss > 0:
            stop_loss = price * (1 - self._config.stop_loss) if is_long else price * (
                1 + self._config.stop_loss
            )

        take_profit = signal.take_profit
        if take_profit is None and self._config.take_profit > 0:
            take_profit = price * (1 + self._config.take_profit) if is_long else price * (
                1 - self._config.take_profit
            )
        return stop_loss, take_profit

    # ------------------------------------------------------------------
    # Überwachung offener Positionen
    # ------------------------------------------------------------------

    def check_exit(self, position: Position, price: float) -> str | None:
        """Prüft SL/TP/Trailing/Break-Even einer offenen Position.

        Mutiert die Position (Trailing-/Break-Even-Nachziehen der Stops,
        Extremwert-Tracking) und meldet, ob ein Exit fällig ist.

        Args:
            position: Offene Position (wird ggf. mutiert).
            price: Aktueller Marktpreis.

        Returns:
            Exit-Grund (``"stop_loss"`` oder ``"take_profit"``) oder None.
        """
        position.update_extremes(price)
        self._apply_break_even(position, price)
        self._apply_trailing_stop(position)

        if position.stop_loss is not None:
            if position.side is PositionSide.LONG and price <= position.stop_loss:
                return "stop_loss"
            if position.side is PositionSide.SHORT and price >= position.stop_loss:
                return "stop_loss"
        if position.take_profit is not None:
            if position.side is PositionSide.LONG and price >= position.take_profit:
                return "take_profit"
            if position.side is PositionSide.SHORT and price <= position.take_profit:
                return "take_profit"
        return None

    def _apply_break_even(self, position: Position, price: float) -> None:
        """Zieht den Stop auf Einstand, sobald der Trigger-Gewinn erreicht ist."""
        trigger = self._config.break_even_trigger
        if trigger <= 0 or position.break_even_done:
            return
        if position.side is PositionSide.LONG:
            if price >= position.entry_price * (1 + trigger):
                new_stop = position.entry_price
                if position.stop_loss is None or new_stop > position.stop_loss:
                    position.stop_loss = new_stop
                position.break_even_done = True
                logger.info("Break-Even: SL von %s auf Einstand %.8f gezogen", position.symbol, new_stop)
        else:
            if price <= position.entry_price * (1 - trigger):
                new_stop = position.entry_price
                if position.stop_loss is None or new_stop < position.stop_loss:
                    position.stop_loss = new_stop
                position.break_even_done = True
                logger.info("Break-Even: SL von %s auf Einstand %.8f gezogen", position.symbol, new_stop)

    def _apply_trailing_stop(self, position: Position) -> None:
        """Zieht den Stop-Loss dem Kurs hinterher (nur in Gewinnrichtung)."""
        delta = position.trailing_stop
        if delta is None or delta <= 0:
            return
        if position.side is PositionSide.LONG:
            candidate = position.highest_price * (1 - delta)
            if position.stop_loss is None or candidate > position.stop_loss:
                position.stop_loss = candidate
        else:
            candidate = position.lowest_price * (1 + delta)
            if position.stop_loss is None or candidate < position.stop_loss:
                position.stop_loss = candidate

    # ------------------------------------------------------------------
    # Intern
    # ------------------------------------------------------------------

    def _roll_day_if_needed(self) -> None:
        """Setzt den Tageszustand bei Datumswechsel (UTC) zurück."""
        today = datetime.now(timezone.utc).date()
        if today != self._daily.day:
            logger.info(
                "Neuer Handelstag %s (Vortag: PnL %.2f, %d Trades)",
                today,
                self._daily.realized_pnl,
                self._daily.trade_count,
            )
            self._daily = _DailyState(day=today, start_equity=self._current_equity)
            # Ein Tagesverlust-Stopp gilt nur für den betroffenen Tag.
            if self._halt_reason and "Tagesverlust" in self._halt_reason:
                self._halt_reason = None
