"""MACD-Strategie.

Kauft beim Kreuzen der MACD-Linie über die Signallinie unterhalb der
Nulllinie (frühes Momentum) bzw. schließt beim umgekehrten Kreuz.
"""

from __future__ import annotations

import pandas as pd

from tradingbot.analytics.indicators import crossover, crossunder, macd
from tradingbot.core.enums import PositionSide, SignalAction
from tradingbot.core.exceptions import StrategyError
from tradingbot.core.models import Signal
from tradingbot.strategies.base import Strategy, StrategyContext
from tradingbot.strategies.registry import register_strategy


@register_strategy("macd")
class MacdStrategy(Strategy):
    """MACD-Signallinien-Crossover.

    Params:
        fast_period: Schnelle EMA (Standard 12).
        slow_period: Langsame EMA (Standard 26).
        signal_period: Signallinien-EMA (Standard 9).
        require_below_zero: Long-Einstieg nur unterhalb der Nulllinie
            (früher Einstieg im Zyklus, Standard False).
        allow_short: Short-Einstiege erlauben (Standard False).
    """

    default_params = {
        "fast_period": 12,
        "slow_period": 26,
        "signal_period": 9,
        "require_below_zero": False,
        "allow_short": False,
    }

    def _validate_params(self) -> None:
        super()._validate_params()
        if self.params["fast_period"] >= self.params["slow_period"]:
            raise StrategyError(f"{self.name}: fast_period muss kleiner als slow_period sein")

    def generate_signal(
        self, df: pd.DataFrame, symbol: str, context: StrategyContext
    ) -> Signal | None:
        if not self.has_enough_history(df):
            return None

        result = macd(
            df["close"],
            int(self.params["fast_period"]),
            int(self.params["slow_period"]),
            int(self.params["signal_period"]),
        )
        macd_line, signal_line = result["macd"], result["signal"]
        if pd.isna(macd_line.iloc[-1]) or pd.isna(signal_line.iloc[-1]):
            return None

        bullish_cross = bool(crossover(macd_line, signal_line).iloc[-1])
        bearish_cross = bool(crossunder(macd_line, signal_line).iloc[-1])
        position = context.position_side(symbol)

        if bullish_cross:
            if self.params["require_below_zero"] and macd_line.iloc[-1] >= 0:
                return None
            if position is PositionSide.SHORT:
                return self.make_signal(SignalAction.CLOSE_SHORT, symbol, df, reason="MACD bullish cross")
            if position is None:
                return self.make_signal(
                    SignalAction.BUY, symbol, df, reason="MACD bullish cross",
                    macd=float(macd_line.iloc[-1]),
                )
        elif bearish_cross:
            if position is PositionSide.LONG:
                return self.make_signal(SignalAction.CLOSE_LONG, symbol, df, reason="MACD bearish cross")
            if position is None and self.params["allow_short"]:
                return self.make_signal(SignalAction.SELL, symbol, df, reason="MACD bearish cross")
        return None
