"""Momentum-Strategie (Rate of Change).

Kauft bei starkem positivem Momentum über der Schwelle und schließt,
sobald das Momentum unter die Exit-Schwelle fällt.
"""

from __future__ import annotations

import pandas as pd

from tradingbot.analytics.indicators import momentum
from tradingbot.core.enums import PositionSide, SignalAction
from tradingbot.core.exceptions import StrategyError
from tradingbot.core.models import Signal
from tradingbot.strategies.base import Strategy, StrategyContext
from tradingbot.strategies.registry import register_strategy


@register_strategy("momentum")
class MomentumStrategy(Strategy):
    """Rate-of-Change-Momentum.

    Params:
        roc_period: Momentum-Fenster in Kerzen (Standard 10).
        entry_threshold: Mindest-ROC für Einstieg (Standard 0.02 = 2 %).
        exit_threshold: ROC, unter dem Long-Positionen geschlossen werden
            (Standard 0.0).
        allow_short: Negatives Momentum shorten (Standard False).
    """

    default_params = {
        "roc_period": 10,
        "entry_threshold": 0.02,
        "exit_threshold": 0.0,
        "allow_short": False,
    }

    def _validate_params(self) -> None:
        super()._validate_params()
        if self.params["entry_threshold"] <= self.params["exit_threshold"]:
            raise StrategyError(f"{self.name}: entry_threshold muss größer als exit_threshold sein")

    def generate_signal(
        self, df: pd.DataFrame, symbol: str, context: StrategyContext
    ) -> Signal | None:
        if not self.has_enough_history(df):
            return None

        roc = momentum(df["close"], int(self.params["roc_period"]))
        current = float(roc.iloc[-1])
        if pd.isna(current):
            return None
        position = context.position_side(symbol)
        entry, exit_thr = float(self.params["entry_threshold"]), float(self.params["exit_threshold"])

        if position is None:
            if current >= entry:
                return self.make_signal(
                    SignalAction.BUY, symbol, df,
                    reason=f"Momentum {current:.2%} >= {entry:.2%}",
                    confidence=min(1.0, current / (entry * 3)),
                    roc=current,
                )
            if current <= -entry and self.params["allow_short"]:
                return self.make_signal(
                    SignalAction.SELL, symbol, df,
                    reason=f"Momentum {current:.2%} <= -{entry:.2%}", roc=current,
                )
        elif position is PositionSide.LONG and current <= exit_thr:
            return self.make_signal(
                SignalAction.CLOSE_LONG, symbol, df, reason=f"Momentum erschöpft ({current:.2%})"
            )
        elif position is PositionSide.SHORT and current >= -exit_thr:
            return self.make_signal(
                SignalAction.CLOSE_SHORT, symbol, df, reason=f"Momentum erschöpft ({current:.2%})"
            )
        return None
