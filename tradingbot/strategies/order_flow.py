"""Order-Flow-Strategie (Orderbuch-Ungleichgewicht).

Nutzt das Bid/Ask-Volumen-Ungleichgewicht des Orderbuchs als primäres
Signal. Steht kein Orderbuch zur Verfügung (z. B. im Backtest), wird als
Fallback ein kerzenbasiertes Volumen-Delta verwendet.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from tradingbot.core.enums import PositionSide, SignalAction
from tradingbot.core.exceptions import StrategyError
from tradingbot.core.models import Signal
from tradingbot.strategies.base import Strategy, StrategyContext
from tradingbot.strategies.registry import register_strategy


@register_strategy("order_flow")
class OrderFlowStrategy(Strategy):
    """Orderbuch-Imbalance mit Kerzen-Fallback.

    Params:
        imbalance_threshold: Mindest-Ungleichgewicht in [0, 1] für einen
            Einstieg (Standard 0.3).
        depth: Berücksichtigte Orderbuch-Levels je Seite (Standard 10).
        delta_period: Fenster des Volumen-Delta-Fallbacks (Standard 20).
        allow_short: Verkaufsdruck shorten (Standard False).
    """

    default_params = {
        "imbalance_threshold": 0.3,
        "depth": 10,
        "delta_period": 20,
        "allow_short": False,
    }

    def _validate_params(self) -> None:
        super()._validate_params()
        if not 0 < self.params["imbalance_threshold"] < 1:
            raise StrategyError(f"{self.name}: imbalance_threshold muss in (0, 1) liegen")

    @property
    def required_history(self) -> int:
        return int(self.params["delta_period"]) * 2

    def _candle_delta(self, df: pd.DataFrame) -> float:
        """Kerzenbasiertes Volumen-Delta in [-1, 1] als Orderbuch-Ersatz.

        Gewichtet das Volumen jeder Kerze mit der Position des Schlusskurses
        innerhalb der Kerzenspanne (Close-Location-Value).
        """
        period = int(self.params["delta_period"])
        window = df.tail(period)
        spans = (window["high"] - window["low"]).replace(0.0, np.nan)
        clv = ((window["close"] - window["low"]) - (window["high"] - window["close"])) / spans
        delta = (clv.fillna(0.0) * window["volume"]).sum()
        total = window["volume"].sum()
        if total <= 0:
            return 0.0
        return float(delta / total)

    def generate_signal(
        self, df: pd.DataFrame, symbol: str, context: StrategyContext
    ) -> Signal | None:
        if not self.has_enough_history(df):
            return None

        order_book = context.get_order_book(symbol)
        if order_book is not None and order_book.bids and order_book.asks:
            imbalance = order_book.imbalance(int(self.params["depth"]))
            source = "orderbook"
        else:
            imbalance = self._candle_delta(df)
            source = "candle_delta"

        threshold = float(self.params["imbalance_threshold"])
        position = context.position_side(symbol)

        if position is None:
            if imbalance >= threshold:
                return self.make_signal(
                    SignalAction.BUY, symbol, df,
                    reason=f"Kaufdruck: Imbalance {imbalance:+.2f} ({source})",
                    confidence=min(1.0, imbalance / (threshold * 2)),
                    imbalance=imbalance, source=source,
                )
            if imbalance <= -threshold and self.params["allow_short"]:
                return self.make_signal(
                    SignalAction.SELL, symbol, df,
                    reason=f"Verkaufsdruck: Imbalance {imbalance:+.2f} ({source})",
                    imbalance=imbalance, source=source,
                )
        elif position is PositionSide.LONG and imbalance <= -threshold / 2:
            return self.make_signal(
                SignalAction.CLOSE_LONG, symbol, df,
                reason=f"Kaufdruck gedreht ({imbalance:+.2f})",
            )
        elif position is PositionSide.SHORT and imbalance >= threshold / 2:
            return self.make_signal(
                SignalAction.CLOSE_SHORT, symbol, df,
                reason=f"Verkaufsdruck gedreht ({imbalance:+.2f})",
            )
        return None
