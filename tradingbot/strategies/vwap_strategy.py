"""VWAP-Strategie.

Kauft, wenn der Kurs deutlich unter dem Intraday-VWAP notiert und wieder
zu ihm zurückkehrt (institutionelles Kaufniveau); Exit bei Rückkehr über
den VWAP bzw. bei definiertem Abstand darüber.
"""

from __future__ import annotations

import pandas as pd

from tradingbot.analytics.indicators import vwap
from tradingbot.core.enums import PositionSide, SignalAction
from tradingbot.core.exceptions import StrategyError
from tradingbot.core.models import Signal
from tradingbot.strategies.base import Strategy, StrategyContext
from tradingbot.strategies.registry import register_strategy


@register_strategy("vwap")
class VwapStrategy(Strategy):
    """Rückkehr zum VWAP.

    Params:
        deviation: Mindestabweichung unter dem VWAP für einen Einstieg
            als Bruchteil (Standard 0.005 = 0.5 %).
        exit_deviation: Abstand über dem VWAP für den Exit (Standard 0.0
            = Exit direkt am VWAP).
        allow_short: Symmetrische Short-Logik über dem VWAP (Standard False).
    """

    default_params = {"deviation": 0.005, "exit_deviation": 0.0, "allow_short": False}

    def _validate_params(self) -> None:
        super()._validate_params()
        if self.params["deviation"] <= 0:
            raise StrategyError(f"{self.name}: deviation muss > 0 sein")

    @property
    def required_history(self) -> int:
        return 30

    def generate_signal(
        self, df: pd.DataFrame, symbol: str, context: StrategyContext
    ) -> Signal | None:
        if not self.has_enough_history(df):
            return None

        vwap_series = vwap(df, reset_daily=True)
        current_vwap = float(vwap_series.iloc[-1])
        if pd.isna(current_vwap) or current_vwap <= 0:
            return None
        close = float(df["close"].iloc[-1])
        prev_close = float(df["close"].iloc[-2])
        prev_vwap = float(vwap_series.iloc[-2])
        deviation = float(self.params["deviation"])
        position = context.position_side(symbol)

        entry_level = current_vwap * (1.0 - deviation)
        prev_entry_level = prev_vwap * (1.0 - deviation)
        exit_level = current_vwap * (1.0 + float(self.params["exit_deviation"]))

        # Kurs kehrt von unten über die Abweichungsschwelle zurück.
        if prev_close < prev_entry_level and close >= entry_level and close < current_vwap:
            if position is PositionSide.SHORT:
                return self.make_signal(SignalAction.CLOSE_SHORT, symbol, df, reason="Rückkehr zum VWAP")
            if position is None:
                return self.make_signal(
                    SignalAction.BUY, symbol, df,
                    reason=f"Kurs {deviation:.1%} unter VWAP, Rückkehr beginnt",
                    take_profit=exit_level,
                    vwap=current_vwap,
                )
        if position is PositionSide.LONG and close >= exit_level:
            return self.make_signal(SignalAction.CLOSE_LONG, symbol, df, reason="VWAP-Ziel erreicht")

        if self.params["allow_short"]:
            short_level = current_vwap * (1.0 + deviation)
            prev_short_level = prev_vwap * (1.0 + deviation)
            if prev_close > prev_short_level and close <= short_level and close > current_vwap:
                if position is None:
                    return self.make_signal(
                        SignalAction.SELL, symbol, df,
                        reason=f"Kurs {deviation:.1%} über VWAP, Rückkehr beginnt",
                        take_profit=current_vwap,
                    )
            if position is PositionSide.SHORT and close <= current_vwap:
                return self.make_signal(SignalAction.CLOSE_SHORT, symbol, df, reason="VWAP erreicht")
        return None
