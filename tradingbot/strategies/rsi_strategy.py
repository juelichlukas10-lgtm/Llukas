"""RSI-Strategie (Mean-Reversion auf Basis von Überkauft/Überverkauft).

Kauft, wenn der RSI aus der überverkauften Zone nach oben dreht, und
schließt/verkauft, wenn er aus der überkauften Zone nach unten dreht.
"""

from __future__ import annotations

import pandas as pd

from tradingbot.analytics.indicators import rsi
from tradingbot.core.enums import PositionSide, SignalAction
from tradingbot.core.exceptions import StrategyError
from tradingbot.core.models import Signal
from tradingbot.strategies.base import Strategy, StrategyContext
from tradingbot.strategies.registry import register_strategy


@register_strategy("rsi")
class RsiStrategy(Strategy):
    """RSI-Umkehrstrategie.

    Params:
        period: RSI-Periode (Standard 14).
        oversold: Überverkauft-Schwelle (Standard 30).
        overbought: Überkauft-Schwelle (Standard 70).
        allow_short: Short-Einstiege erlauben (Standard False).
    """

    default_params = {"period": 14, "oversold": 30.0, "overbought": 70.0, "allow_short": False}

    def _validate_params(self) -> None:
        super()._validate_params()
        if not 0 < self.params["oversold"] < self.params["overbought"] < 100:
            raise StrategyError(
                f"{self.name}: 0 < oversold < overbought < 100 verletzt "
                f"({self.params['oversold']}/{self.params['overbought']})"
            )

    @property
    def required_history(self) -> int:
        return int(self.params["period"]) * 3

    def generate_signal(
        self, df: pd.DataFrame, symbol: str, context: StrategyContext
    ) -> Signal | None:
        if not self.has_enough_history(df):
            return None

        values = rsi(df["close"], int(self.params["period"]))
        current, previous = float(values.iloc[-1]), float(values.iloc[-2])
        if pd.isna(current) or pd.isna(previous):
            return None
        position = context.position_side(symbol)
        oversold, overbought = self.params["oversold"], self.params["overbought"]

        # Aufwärtsdrehung aus der überverkauften Zone.
        if previous <= oversold < current:
            if position is PositionSide.SHORT:
                return self.make_signal(
                    SignalAction.CLOSE_SHORT, symbol, df,
                    reason=f"RSI dreht aus überverkauft ({current:.1f})", rsi=current,
                )
            if position is None:
                return self.make_signal(
                    SignalAction.BUY, symbol, df,
                    reason=f"RSI dreht aus überverkauft ({current:.1f})",
                    confidence=min(1.0, (oversold - min(previous, oversold) + 10) / 20),
                    rsi=current,
                )
        # Abwärtsdrehung aus der überkauften Zone.
        elif previous >= overbought > current:
            if position is PositionSide.LONG:
                return self.make_signal(
                    SignalAction.CLOSE_LONG, symbol, df,
                    reason=f"RSI dreht aus überkauft ({current:.1f})", rsi=current,
                )
            if position is None and self.params["allow_short"]:
                return self.make_signal(
                    SignalAction.SELL, symbol, df,
                    reason=f"RSI dreht aus überkauft ({current:.1f})", rsi=current,
                )
        return None
