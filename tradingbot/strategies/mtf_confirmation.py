"""Multi-Timeframe-Bestätigungsstrategie.

Handelt Einstiege auf dem Haupt-Timeframe nur, wenn der übergeordnete
Timeframe den Trend bestätigt: Der höhere Timeframe liefert die
Richtung (EMA-Filter), der niedrigere den Einstiegszeitpunkt
(RSI-Rücksetzer im Trend).
"""

from __future__ import annotations

import pandas as pd

from tradingbot.analytics.indicators import ema, rsi
from tradingbot.core.enums import PositionSide, SignalAction, Timeframe
from tradingbot.core.exceptions import StrategyError
from tradingbot.core.models import Signal
from tradingbot.strategies.base import Strategy, StrategyContext
from tradingbot.strategies.registry import register_strategy


@register_strategy("mtf_confirmation")
class MtfConfirmationStrategy(Strategy):
    """Höherer Timeframe bestätigt, niedrigerer triggert.

    Params:
        higher_timeframe: Bestätigungs-Timeframe (Standard "1h").
        trend_period: EMA-Periode auf dem höheren Timeframe (Standard 50).
        rsi_period: RSI-Periode auf dem Haupt-Timeframe (Standard 14).
        rsi_entry: RSI-Schwelle für Rücksetzer im Aufwärtstrend (Standard 40).
        rsi_exit: RSI-Schwelle für den Exit (Standard 70).
        allow_short: Symmetrische Short-Logik (Standard False).
    """

    default_params = {
        "higher_timeframe": "1h",
        "trend_period": 50,
        "rsi_period": 14,
        "rsi_entry": 40.0,
        "rsi_exit": 70.0,
        "allow_short": False,
    }

    def _validate_params(self) -> None:
        super()._validate_params()
        higher = Timeframe.from_string(str(self.params["higher_timeframe"]))
        if higher.seconds <= self.timeframe.seconds:
            raise StrategyError(
                f"{self.name}: higher_timeframe ({higher.value}) muss gröber als der "
                f"Haupt-Timeframe ({self.timeframe.value}) sein"
            )

    @property
    def higher_timeframe(self) -> Timeframe:
        """Der konfigurierte Bestätigungs-Timeframe."""
        return Timeframe.from_string(str(self.params["higher_timeframe"]))

    @property
    def additional_timeframes(self) -> list[Timeframe]:
        return [self.higher_timeframe]

    def generate_signal(
        self, df: pd.DataFrame, symbol: str, context: StrategyContext
    ) -> Signal | None:
        if not self.has_enough_history(df):
            return None

        higher_df = context.get_candles(symbol, self.higher_timeframe)
        trend_period = int(self.params["trend_period"])
        if len(higher_df) < trend_period:
            return None

        higher_ema = ema(higher_df["close"], trend_period)
        if pd.isna(higher_ema.iloc[-1]):
            return None
        higher_close = float(higher_df["close"].iloc[-1])
        uptrend = higher_close > float(higher_ema.iloc[-1])

        rsi_series = rsi(df["close"], int(self.params["rsi_period"]))
        current_rsi, prev_rsi = float(rsi_series.iloc[-1]), float(rsi_series.iloc[-2])
        if pd.isna(current_rsi) or pd.isna(prev_rsi):
            return None

        position = context.position_side(symbol)
        rsi_entry, rsi_exit = float(self.params["rsi_entry"]), float(self.params["rsi_exit"])

        if position is None:
            # Rücksetzer im bestätigten Aufwärtstrend.
            if uptrend and prev_rsi <= rsi_entry < current_rsi:
                return self.make_signal(
                    SignalAction.BUY, symbol, df,
                    reason=f"HTF-Aufwärtstrend + RSI-Rücksetzer dreht ({current_rsi:.0f})",
                    htf_trend="up", rsi=current_rsi,
                )
            if not uptrend and self.params["allow_short"] and prev_rsi >= (100 - rsi_entry) > current_rsi:
                return self.make_signal(
                    SignalAction.SELL, symbol, df,
                    reason=f"HTF-Abwärtstrend + RSI-Erholung dreht ({current_rsi:.0f})",
                    htf_trend="down", rsi=current_rsi,
                )
        elif position is PositionSide.LONG:
            if not uptrend:
                return self.make_signal(SignalAction.CLOSE_LONG, symbol, df, reason="HTF-Trend gedreht")
            if current_rsi >= rsi_exit:
                return self.make_signal(
                    SignalAction.CLOSE_LONG, symbol, df, reason=f"RSI-Ziel erreicht ({current_rsi:.0f})"
                )
        elif position is PositionSide.SHORT:
            if uptrend:
                return self.make_signal(SignalAction.CLOSE_SHORT, symbol, df, reason="HTF-Trend gedreht")
            if current_rsi <= (100 - rsi_exit):
                return self.make_signal(
                    SignalAction.CLOSE_SHORT, symbol, df, reason=f"RSI-Ziel erreicht ({current_rsi:.0f})"
                )
        return None
