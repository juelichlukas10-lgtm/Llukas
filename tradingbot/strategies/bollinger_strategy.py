"""Bollinger-Band-Strategie (Mean Reversion).

Kauft, wenn der Kurs unter das untere Band fällt und wieder hineinkehrt;
schließt an der Mittellinie bzw. am oberen Band.
"""

from __future__ import annotations

import pandas as pd

from tradingbot.analytics.indicators import bollinger_bands
from tradingbot.core.enums import PositionSide, SignalAction
from tradingbot.core.models import Signal
from tradingbot.strategies.base import Strategy, StrategyContext
from tradingbot.strategies.registry import register_strategy


@register_strategy("bollinger")
class BollingerStrategy(Strategy):
    """Rückkehr zum Mittelwert über Bollinger-Bänder.

    Params:
        bb_period: Bandperiode (Standard 20).
        std_dev: Standardabweichungs-Multiplikator (Standard 2.0).
        exit_at_middle: Long-Exit an der Mittellinie statt am oberen Band
            (Standard True).
        allow_short: Short-Einstiege am oberen Band erlauben (Standard False).
    """

    default_params = {"bb_period": 20, "std_dev": 2.0, "exit_at_middle": True, "allow_short": False}

    def generate_signal(
        self, df: pd.DataFrame, symbol: str, context: StrategyContext
    ) -> Signal | None:
        if not self.has_enough_history(df):
            return None

        bands = bollinger_bands(df["close"], int(self.params["bb_period"]), float(self.params["std_dev"]))
        if bands.dropna().empty:
            return None
        close = float(df["close"].iloc[-1])
        prev_close = float(df["close"].iloc[-2])
        lower, upper, middle = (
            float(bands["lower"].iloc[-1]),
            float(bands["upper"].iloc[-1]),
            float(bands["middle"].iloc[-1]),
        )
        prev_lower, prev_upper = float(bands["lower"].iloc[-2]), float(bands["upper"].iloc[-2])
        position = context.position_side(symbol)

        # Wiedereintritt von unten ins Band -> Long.
        if prev_close < prev_lower and close >= lower:
            if position is PositionSide.SHORT:
                return self.make_signal(SignalAction.CLOSE_SHORT, symbol, df, reason="Rückkehr ins Band")
            if position is None:
                return self.make_signal(
                    SignalAction.BUY, symbol, df,
                    reason="Rückkehr über unteres Bollinger-Band",
                    stop_loss=min(float(df["low"].iloc[-2]), lower * 0.995),
                    take_profit=middle if self.params["exit_at_middle"] else upper,
                )
        # Long-Exit: Mittellinie bzw. oberes Band erreicht.
        if position is PositionSide.LONG:
            target = middle if self.params["exit_at_middle"] else upper
            if close >= target:
                return self.make_signal(SignalAction.CLOSE_LONG, symbol, df, reason="Bollinger-Ziel erreicht")
        # Wiedereintritt von oben ins Band -> Short.
        if prev_close > prev_upper and close <= upper:
            if position is None and self.params["allow_short"]:
                return self.make_signal(
                    SignalAction.SELL, symbol, df,
                    reason="Rückkehr unter oberes Bollinger-Band",
                    stop_loss=max(float(df["high"].iloc[-2]), upper * 1.005),
                    take_profit=middle if self.params["exit_at_middle"] else lower,
                )
        if position is PositionSide.SHORT:
            target = middle if self.params["exit_at_middle"] else lower
            if close <= target:
                return self.make_signal(SignalAction.CLOSE_SHORT, symbol, df, reason="Bollinger-Ziel erreicht")
        return None
