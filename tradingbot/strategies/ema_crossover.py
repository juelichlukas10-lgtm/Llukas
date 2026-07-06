"""EMA-Crossover-Strategie.

Kauft, wenn die schnelle EMA die langsame von unten kreuzt (Golden
Cross), und verkauft/schließt beim umgekehrten Kreuz (Death Cross).
"""

from __future__ import annotations

import pandas as pd

from tradingbot.analytics.indicators import crossover, crossunder, ema
from tradingbot.core.enums import PositionSide, SignalAction
from tradingbot.core.exceptions import StrategyError
from tradingbot.core.models import Signal
from tradingbot.strategies.base import Strategy, StrategyContext
from tradingbot.strategies.registry import register_strategy


@register_strategy("ema_crossover")
class EmaCrossoverStrategy(Strategy):
    """Klassischer Trendfolger auf Basis zweier EMAs.

    Params:
        fast_period: Periode der schnellen EMA (Standard 12).
        slow_period: Periode der langsamen EMA (Standard 26).
        allow_short: Short-Einstiege erlauben (Standard False).
    """

    default_params = {"fast_period": 12, "slow_period": 26, "allow_short": False}

    def _validate_params(self) -> None:
        super()._validate_params()
        if self.params["fast_period"] >= self.params["slow_period"]:
            raise StrategyError(
                f"{self.name}: fast_period ({self.params['fast_period']}) muss kleiner "
                f"als slow_period ({self.params['slow_period']}) sein"
            )

    def generate_signal(
        self, df: pd.DataFrame, symbol: str, context: StrategyContext
    ) -> Signal | None:
        if not self.has_enough_history(df):
            return None

        fast = ema(df["close"], self.params["fast_period"])
        slow = ema(df["close"], self.params["slow_period"])
        golden_cross = bool(crossover(fast, slow).iloc[-1])
        death_cross = bool(crossunder(fast, slow).iloc[-1])
        position = context.position_side(symbol)

        if golden_cross:
            if position is PositionSide.SHORT:
                return self.make_signal(
                    SignalAction.CLOSE_SHORT, symbol, df, reason="EMA Golden Cross"
                )
            if position is None:
                return self.make_signal(SignalAction.BUY, symbol, df, reason="EMA Golden Cross")
        elif death_cross:
            if position is PositionSide.LONG:
                return self.make_signal(
                    SignalAction.CLOSE_LONG, symbol, df, reason="EMA Death Cross"
                )
            if position is None and self.params["allow_short"]:
                return self.make_signal(SignalAction.SELL, symbol, df, reason="EMA Death Cross")
        return None
