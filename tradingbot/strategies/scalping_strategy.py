"""Scalping-Strategie für kurze Timeframes.

Kombiniert eine schnelle EMA-Richtung mit dem Stochastic-Oszillator:
Einstieg bei kurzfristig überverkauftem Markt im Mikro-Aufwärtstrend,
enge Gewinnziele und Stops.
"""

from __future__ import annotations

import pandas as pd

from tradingbot.analytics.indicators import ema, stochastic
from tradingbot.core.enums import PositionSide, SignalAction
from tradingbot.core.exceptions import StrategyError
from tradingbot.core.models import Signal
from tradingbot.strategies.base import Strategy, StrategyContext
from tradingbot.strategies.registry import register_strategy


@register_strategy("scalping")
class ScalpingStrategy(Strategy):
    """EMA + Stochastic Scalper mit engen Zielen.

    Params:
        ema_period: Trend-EMA (Standard 21).
        stoch_period: Stochastic %K-Periode (Standard 14).
        stoch_oversold: Einstiegsschwelle (Standard 20).
        stoch_overbought: Exit-/Short-Schwelle (Standard 80).
        target_pct: Gewinnziel als Bruchteil (Standard 0.004 = 0.4 %).
        stop_pct: Stop-Loss als Bruchteil (Standard 0.003 = 0.3 %).
        allow_short: Short-Scalps erlauben (Standard False).
    """

    default_params = {
        "ema_period": 21,
        "stoch_period": 14,
        "stoch_oversold": 20.0,
        "stoch_overbought": 80.0,
        "target_pct": 0.004,
        "stop_pct": 0.003,
        "allow_short": False,
    }

    def _validate_params(self) -> None:
        super()._validate_params()
        if self.params["target_pct"] <= 0 or self.params["stop_pct"] <= 0:
            raise StrategyError(f"{self.name}: target_pct und stop_pct müssen > 0 sein")

    def generate_signal(
        self, df: pd.DataFrame, symbol: str, context: StrategyContext
    ) -> Signal | None:
        if not self.has_enough_history(df):
            return None

        trend = ema(df["close"], int(self.params["ema_period"]))
        stoch = stochastic(df, int(self.params["stoch_period"]))
        k, prev_k = float(stoch["k"].iloc[-1]), float(stoch["k"].iloc[-2])
        if pd.isna(k) or pd.isna(prev_k) or pd.isna(trend.iloc[-1]):
            return None

        close = float(df["close"].iloc[-1])
        uptrend = close > float(trend.iloc[-1])
        position = context.position_side(symbol)
        oversold = float(self.params["stoch_oversold"])
        overbought = float(self.params["stoch_overbought"])

        if position is None:
            # Long-Scalp: Stochastic dreht im Aufwärtstrend aus der überverkauften Zone.
            if uptrend and prev_k <= oversold < k:
                return self.make_signal(
                    SignalAction.BUY, symbol, df,
                    reason=f"Scalp: Stoch dreht auf ({k:.0f}) im Aufwärtstrend",
                    take_profit=close * (1.0 + float(self.params["target_pct"])),
                    stop_loss=close * (1.0 - float(self.params["stop_pct"])),
                    stoch_k=k,
                )
            if not uptrend and prev_k >= overbought > k and self.params["allow_short"]:
                return self.make_signal(
                    SignalAction.SELL, symbol, df,
                    reason=f"Scalp: Stoch dreht ab ({k:.0f}) im Abwärtstrend",
                    take_profit=close * (1.0 - float(self.params["target_pct"])),
                    stop_loss=close * (1.0 + float(self.params["stop_pct"])),
                    stoch_k=k,
                )
        elif position is PositionSide.LONG and k >= overbought:
            return self.make_signal(
                SignalAction.CLOSE_LONG, symbol, df, reason=f"Scalp-Exit: Stoch überkauft ({k:.0f})"
            )
        elif position is PositionSide.SHORT and k <= oversold:
            return self.make_signal(
                SignalAction.CLOSE_SHORT, symbol, df, reason=f"Scalp-Exit: Stoch überverkauft ({k:.0f})"
            )
        return None
