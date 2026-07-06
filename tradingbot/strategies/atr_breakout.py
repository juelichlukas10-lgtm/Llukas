"""ATR-Breakout-Strategie (Volatilitätsausbruch).

Kauft, wenn der Schlusskurs mehr als ``multiplier`` × ATR über dem
vorherigen Schlusskurs liegt (impulsiver Ausbruch); Exit bei
Gegenbewegung um denselben ATR-Abstand.
"""

from __future__ import annotations

import pandas as pd

from tradingbot.analytics.indicators import atr
from tradingbot.core.enums import PositionSide, SignalAction
from tradingbot.core.exceptions import StrategyError
from tradingbot.core.models import Signal
from tradingbot.strategies.base import Strategy, StrategyContext
from tradingbot.strategies.registry import register_strategy


@register_strategy("atr_breakout")
class AtrBreakoutStrategy(Strategy):
    """Volatilitätsausbruch auf ATR-Basis.

    Params:
        atr_period: ATR-Periode (Standard 14).
        multiplier: ATR-Multiplikator für den Ausbruch (Standard 1.5).
        allow_short: Abwärtsausbrüche shorten (Standard False).
    """

    default_params = {"atr_period": 14, "multiplier": 1.5, "allow_short": False}

    def _validate_params(self) -> None:
        super()._validate_params()
        if self.params["multiplier"] <= 0:
            raise StrategyError(f"{self.name}: multiplier muss > 0 sein")

    def generate_signal(
        self, df: pd.DataFrame, symbol: str, context: StrategyContext
    ) -> Signal | None:
        if not self.has_enough_history(df):
            return None

        atr_series = atr(df, int(self.params["atr_period"]))
        current_atr = float(atr_series.iloc[-2])  # ATR vor der Ausbruchskerze
        if pd.isna(current_atr) or current_atr <= 0:
            return None
        close = float(df["close"].iloc[-1])
        prev_close = float(df["close"].iloc[-2])
        threshold = float(self.params["multiplier"]) * current_atr
        move = close - prev_close
        position = context.position_side(symbol)

        if move > threshold:
            if position is PositionSide.SHORT:
                return self.make_signal(SignalAction.CLOSE_SHORT, symbol, df, reason="ATR-Ausbruch aufwärts")
            if position is None:
                return self.make_signal(
                    SignalAction.BUY, symbol, df,
                    reason=f"Ausbruch +{move / current_atr:.1f} ATR",
                    stop_loss=close - threshold,
                    atr=current_atr,
                )
        elif move < -threshold:
            if position is PositionSide.LONG:
                return self.make_signal(SignalAction.CLOSE_LONG, symbol, df, reason="ATR-Ausbruch abwärts")
            if position is None and self.params["allow_short"]:
                return self.make_signal(
                    SignalAction.SELL, symbol, df,
                    reason=f"Ausbruch {move / current_atr:.1f} ATR",
                    stop_loss=close + threshold,
                    atr=current_atr,
                )
        return None
