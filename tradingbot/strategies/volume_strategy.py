"""Volumen-Strategie (Volume Spike + OBV-Bestätigung).

Kauft, wenn eine bullische Kerze mit außergewöhnlich hohem Volumen
auftritt und der OBV-Trend die Akkumulation bestätigt; Exit bei
bearischem Volume-Spike oder OBV-Trendbruch.
"""

from __future__ import annotations

import pandas as pd

from tradingbot.analytics.indicators import obv, sma
from tradingbot.core.enums import PositionSide, SignalAction
from tradingbot.core.exceptions import StrategyError
from tradingbot.core.models import Signal
from tradingbot.strategies.base import Strategy, StrategyContext
from tradingbot.strategies.registry import register_strategy


@register_strategy("volume")
class VolumeStrategy(Strategy):
    """Volume-Spike-Strategie mit OBV-Filter.

    Params:
        volume_period: Fenster für das Durchschnittsvolumen (Standard 20).
        spike_factor: Mindestfaktor Volumen vs. Durchschnitt (Standard 2.0).
        obv_period: SMA-Periode der OBV-Trendbestimmung (Standard 20).
        allow_short: Bearische Spikes shorten (Standard False).
    """

    default_params = {
        "volume_period": 20,
        "spike_factor": 2.0,
        "obv_period": 20,
        "allow_short": False,
    }

    def _validate_params(self) -> None:
        super()._validate_params()
        if self.params["spike_factor"] <= 1.0:
            raise StrategyError(f"{self.name}: spike_factor muss > 1.0 sein")

    def generate_signal(
        self, df: pd.DataFrame, symbol: str, context: StrategyContext
    ) -> Signal | None:
        if not self.has_enough_history(df):
            return None

        avg_volume = sma(df["volume"], int(self.params["volume_period"]))
        if pd.isna(avg_volume.iloc[-2]) or float(avg_volume.iloc[-2]) <= 0:
            return None
        volume_ratio = float(df["volume"].iloc[-1]) / float(avg_volume.iloc[-2])
        spike = volume_ratio >= float(self.params["spike_factor"])

        obv_series = obv(df)
        obv_trend = sma(obv_series, int(self.params["obv_period"]))
        if pd.isna(obv_trend.iloc[-1]):
            return None
        obv_rising = float(obv_series.iloc[-1]) > float(obv_trend.iloc[-1])

        last = df.iloc[-1]
        bullish_candle = float(last["close"]) > float(last["open"])
        position = context.position_side(symbol)

        if spike and bullish_candle and obv_rising:
            if position is PositionSide.SHORT:
                return self.make_signal(
                    SignalAction.CLOSE_SHORT, symbol, df, reason="Bullischer Volume-Spike"
                )
            if position is None:
                return self.make_signal(
                    SignalAction.BUY, symbol, df,
                    reason=f"Volume-Spike {volume_ratio:.1f}x mit OBV-Bestätigung",
                    confidence=min(1.0, volume_ratio / (float(self.params["spike_factor"]) * 2)),
                    volume_ratio=volume_ratio,
                )
        elif spike and not bullish_candle:
            if position is PositionSide.LONG:
                return self.make_signal(
                    SignalAction.CLOSE_LONG, symbol, df,
                    reason=f"Bearischer Volume-Spike {volume_ratio:.1f}x",
                )
            if position is None and self.params["allow_short"] and not obv_rising:
                return self.make_signal(
                    SignalAction.SELL, symbol, df,
                    reason=f"Bearischer Volume-Spike {volume_ratio:.1f}x mit OBV-Bestätigung",
                    volume_ratio=volume_ratio,
                )
        elif position is PositionSide.LONG and not obv_rising:
            return self.make_signal(SignalAction.CLOSE_LONG, symbol, df, reason="OBV-Trend gebrochen")
        return None
