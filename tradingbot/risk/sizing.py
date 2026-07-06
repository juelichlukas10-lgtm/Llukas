"""Positionsgrößen-Berechnung.

Unterstützte Verfahren (:class:`~tradingbot.core.enums.SizingMethod`):

    * ``fixed`` – fester Quote-Betrag pro Trade.
    * ``percent_risk`` – riskiert einen festen Prozentsatz des Kapitals,
      abgeleitet aus der Stop-Loss-Distanz.
    * ``kelly`` – Kelly-Kriterium auf Basis der jüngsten Trade-Historie,
      gedämpft über ``kelly_fraction``.
    * ``atr`` – Positionsgröße aus der ATR-basierten Stop-Distanz.

Alle Verfahren begrenzen die Größe auf das verfügbare Kapital × Hebel.
"""

from __future__ import annotations

from tradingbot.core.config import RiskConfig, SizingConfig
from tradingbot.core.enums import SizingMethod
from tradingbot.core.exceptions import RiskError
from tradingbot.core.logging import get_logger
from tradingbot.core.models import Trade

logger = get_logger(__name__)


class PositionSizer:
    """Berechnet Positionsgrößen nach dem konfigurierten Verfahren.

    Args:
        sizing: Sizing-Konfiguration (Methode und Parameter).
        risk: Risiko-Konfiguration (risk_per_trade, Fallback-Stop, Hebel).
    """

    def __init__(self, sizing: SizingConfig, risk: RiskConfig) -> None:
        self._sizing = sizing
        self._risk = risk

    def compute(
        self,
        equity: float,
        price: float,
        stop_loss: float | None = None,
        atr_value: float | None = None,
        trade_history: list[Trade] | None = None,
        max_leverage: float | None = None,
    ) -> float:
        """Berechnet die Positionsgröße in Basis-Einheiten.

        Args:
            equity: Aktuelles Gesamtkapital in Quote-Währung.
            price: Aktueller Einstiegspreis.
            stop_loss: Geplanter Stop-Loss-Preis (für percent_risk).
            atr_value: Aktueller ATR-Wert (für atr-Sizing).
            trade_history: Jüngste abgeschlossene Trades (für Kelly).
            max_leverage: Hebel-Obergrenze (None = aus der Risiko-Config).

        Returns:
            Positionsgröße in Basis-Einheiten (>= 0).

        Raises:
            RiskError: Bei ungültigen Eingaben (equity/price <= 0).
        """
        if equity <= 0:
            raise RiskError(f"Ungültiges Kapital: {equity}")
        if price <= 0:
            raise RiskError(f"Ungültiger Preis: {price}")

        leverage_cap = max_leverage if max_leverage is not None else self._risk.max_leverage

        match self._sizing.method:
            case SizingMethod.FIXED:
                amount = self._sizing.fixed_amount / price
            case SizingMethod.PERCENT_RISK:
                amount = self._percent_risk(equity, price, stop_loss)
            case SizingMethod.KELLY:
                amount = self._kelly(equity, price, trade_history or [])
            case SizingMethod.ATR:
                amount = self._atr(equity, price, atr_value)
            case _:
                raise RiskError(f"Unbekannte Sizing-Methode: {self._sizing.method}")

        # Obergrenze: Notional darf Kapital × Hebel nicht überschreiten.
        max_amount = equity * leverage_cap / price
        if amount > max_amount:
            logger.debug(
                "Positionsgröße %.8f auf Hebel-Limit %.8f gekappt", amount, max_amount
            )
            amount = max_amount
        return max(amount, 0.0)

    def _percent_risk(self, equity: float, price: float, stop_loss: float | None) -> float:
        """Risiko-Prozent-Sizing: Verlust bei Stop = risk_per_trade × Kapital."""
        risk_amount = equity * self._risk.risk_per_trade
        if stop_loss is not None and stop_loss > 0:
            stop_distance = abs(price - stop_loss)
        else:
            stop_distance = price * self._risk.stop_loss
        if stop_distance <= 0:
            # Kein sinnvoller Stop ableitbar -> konservativ das Risiko-Budget einsetzen.
            return risk_amount / price
        return risk_amount / stop_distance

    def _kelly(self, equity: float, price: float, history: list[Trade]) -> float:
        """Kelly-Kriterium: f* = W − (1−W)/R, gedämpft mit kelly_fraction."""
        lookback = self._sizing.kelly_lookback
        recent = history[-lookback:]
        if len(recent) < 5:
            # Zu wenig Historie -> konservativer Fallback auf percent_risk.
            logger.debug("Kelly: nur %d Trades verfügbar, Fallback auf percent_risk", len(recent))
            return self._percent_risk(equity, price, None)

        wins = [t.pnl for t in recent if t.pnl > 0]
        losses = [-t.pnl for t in recent if t.pnl < 0]
        if not wins:
            return 0.0
        if not losses:
            # Nur Gewinne: Kelly wäre 1.0 – auf risk_per_trade-Vielfaches begrenzen.
            fraction = self._sizing.kelly_fraction
        else:
            win_rate = len(wins) / len(recent)
            avg_win = sum(wins) / len(wins)
            avg_loss = sum(losses) / len(losses)
            payoff = avg_win / avg_loss if avg_loss > 0 else 1.0
            kelly = win_rate - (1.0 - win_rate) / payoff
            fraction = max(kelly, 0.0) * self._sizing.kelly_fraction
        return equity * fraction / price

    def _atr(self, equity: float, price: float, atr_value: float | None) -> float:
        """ATR-Sizing: Stop-Distanz = atr_risk_multiple × ATR."""
        if atr_value is None or atr_value <= 0:
            logger.debug("ATR-Sizing ohne ATR-Wert – Fallback auf percent_risk")
            return self._percent_risk(equity, price, None)
        risk_amount = equity * self._risk.risk_per_trade
        stop_distance = self._sizing.atr_risk_multiple * atr_value
        return risk_amount / stop_distance
