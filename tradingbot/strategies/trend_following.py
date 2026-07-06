"""Trendfolge-Strategie mit ADX-Filter.

Kombiniert eine EMA-Trendrichtung mit dem ADX als Trendstärke-Filter:
Nur wenn ein starker Trend vorliegt (ADX über Schwelle), werden
Positionen in Trendrichtung eröffnet.
"""

from __future__ import annotations

import pandas as pd

from tradingbot.analytics.indicators import adx, ema
from tradingbot.core.enums import PositionSide, SignalAction
from tradingbot.core.exceptions import StrategyError
from tradingbot.core.models import Signal
from tradingbot.strategies.base import Strategy, StrategyContext
from tradingbot.strategies.registry import register_strategy


@register_strategy("trend_following")
class TrendFollowingStrategy(Strategy):
    """EMA-Trend mit ADX-Stärkefilter.

    Params:
        trend_period: EMA-Periode der Trendbestimmung (Standard 50).
        adx_period: ADX-Periode (Standard 14).
        adx_threshold: Mindest-ADX für einen validen Trend (Standard 25).
        allow_short: Abwärtstrends shorten (Standard False).
    """

    default_params = {
        "trend_period": 50,
        "adx_period": 14,
        "adx_threshold": 25.0,
        "allow_short": False,
    }

    def _validate_params(self) -> None:
        super()._validate_params()
        if not 0 < self.params["adx_threshold"] < 100:
            raise StrategyError(f"{self.name}: adx_threshold muss in (0, 100) liegen")

    def generate_signal(
        self, df: pd.DataFrame, symbol: str, context: StrategyContext
    ) -> Signal | None:
        if not self.has_enough_history(df):
            return None

        trend_ema = ema(df["close"], int(self.params["trend_period"]))
        adx_data = adx(df, int(self.params["adx_period"]))
        current_adx = float(adx_data["adx"].iloc[-1])
        plus_di = float(adx_data["plus_di"].iloc[-1])
        minus_di = float(adx_data["minus_di"].iloc[-1])
        if pd.isna(current_adx) or pd.isna(trend_ema.iloc[-1]):
            return None

        close = float(df["close"].iloc[-1])
        above_trend = close > float(trend_ema.iloc[-1])
        strong_trend = current_adx >= float(self.params["adx_threshold"])
        position = context.position_side(symbol)

        if position is None and strong_trend:
            if above_trend and plus_di > minus_di:
                return self.make_signal(
                    SignalAction.BUY, symbol, df,
                    reason=f"Starker Aufwärtstrend (ADX {current_adx:.0f})",
                    confidence=min(1.0, current_adx / 50.0),
                    adx=current_adx,
                )
            if not above_trend and minus_di > plus_di and self.params["allow_short"]:
                return self.make_signal(
                    SignalAction.SELL, symbol, df,
                    reason=f"Starker Abwärtstrend (ADX {current_adx:.0f})",
                    adx=current_adx,
                )
        elif position is PositionSide.LONG and (not above_trend or not strong_trend):
            return self.make_signal(
                SignalAction.CLOSE_LONG, symbol, df,
                reason="Trend gebrochen oder abgeschwächt",
            )
        elif position is PositionSide.SHORT and (above_trend or not strong_trend):
            return self.make_signal(
                SignalAction.CLOSE_SHORT, symbol, df,
                reason="Trend gebrochen oder abgeschwächt",
            )
        return None
