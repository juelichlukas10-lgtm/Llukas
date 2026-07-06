"""Supertrend-Strategie.

Folgt der Richtung des Supertrend-Indikators: Long bei Wechsel auf
Aufwärtstrend, Exit/Short bei Wechsel auf Abwärtstrend. Die
Supertrend-Linie dient als natürlicher Stop-Loss.
"""

from __future__ import annotations

import pandas as pd

from tradingbot.analytics.indicators import supertrend
from tradingbot.core.enums import PositionSide, SignalAction
from tradingbot.core.models import Signal
from tradingbot.strategies.base import Strategy, StrategyContext
from tradingbot.strategies.registry import register_strategy


@register_strategy("supertrend")
class SupertrendStrategy(Strategy):
    """Trendfolge über den Supertrend-Indikator.

    Params:
        st_period: ATR-Periode des Supertrends (Standard 10).
        multiplier: ATR-Multiplikator (Standard 3.0).
        allow_short: Short-Einstiege erlauben (Standard False).
    """

    default_params = {"st_period": 10, "multiplier": 3.0, "allow_short": False}

    def generate_signal(
        self, df: pd.DataFrame, symbol: str, context: StrategyContext
    ) -> Signal | None:
        if not self.has_enough_history(df):
            return None

        st = supertrend(df, int(self.params["st_period"]), float(self.params["multiplier"]))
        direction, prev_direction = float(st["direction"].iloc[-1]), float(st["direction"].iloc[-2])
        st_line = float(st["supertrend"].iloc[-1])
        if direction == 0.0 or prev_direction == 0.0 or pd.isna(st_line):
            return None
        position = context.position_side(symbol)

        flipped_up = prev_direction < 0 < direction
        flipped_down = prev_direction > 0 > direction

        if flipped_up:
            if position is PositionSide.SHORT:
                return self.make_signal(SignalAction.CLOSE_SHORT, symbol, df, reason="Supertrend dreht auf long")
            if position is None:
                return self.make_signal(
                    SignalAction.BUY, symbol, df,
                    reason="Supertrend dreht auf long", stop_loss=st_line, supertrend=st_line,
                )
        elif flipped_down:
            if position is PositionSide.LONG:
                return self.make_signal(SignalAction.CLOSE_LONG, symbol, df, reason="Supertrend dreht auf short")
            if position is None and self.params["allow_short"]:
                return self.make_signal(
                    SignalAction.SELL, symbol, df,
                    reason="Supertrend dreht auf short", stop_loss=st_line, supertrend=st_line,
                )
        return None
