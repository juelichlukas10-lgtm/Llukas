"""Breakout-Strategie mit Volumenbestätigung.

Kauft beim Ausbruch über das N-Kerzen-Hoch, sofern das Volumen der
Ausbruchskerze deutlich über dem Durchschnitt liegt (echter Ausbruch
statt Fehlsignal).
"""

from __future__ import annotations

import pandas as pd

from tradingbot.core.enums import PositionSide, SignalAction
from tradingbot.core.exceptions import StrategyError
from tradingbot.core.models import Signal
from tradingbot.strategies.base import Strategy, StrategyContext
from tradingbot.strategies.registry import register_strategy


@register_strategy("breakout")
class BreakoutStrategy(Strategy):
    """Range-Ausbruch mit Volumenfilter.

    Params:
        lookback_period: Fenster für Hoch/Tief (Standard 20).
        volume_factor: Mindestfaktor Volumen vs. Durchschnitt (Standard 1.5).
        stop_lookback: Fenster für den initialen Stop (Standard 10).
        allow_short: Abwärtsausbrüche shorten (Standard False).
    """

    default_params = {
        "lookback_period": 20,
        "volume_factor": 1.5,
        "stop_lookback": 10,
        "allow_short": False,
    }

    def _validate_params(self) -> None:
        super()._validate_params()
        if self.params["volume_factor"] < 1.0:
            raise StrategyError(f"{self.name}: volume_factor muss >= 1.0 sein")

    def generate_signal(
        self, df: pd.DataFrame, symbol: str, context: StrategyContext
    ) -> Signal | None:
        if not self.has_enough_history(df):
            return None

        lookback = int(self.params["lookback_period"])
        stop_lookback = int(self.params["stop_lookback"])
        history = df.iloc[:-1]  # Referenzbereich ohne aktuelle Kerze

        range_high = float(history["high"].tail(lookback).max())
        range_low = float(history["low"].tail(lookback).min())
        avg_volume = float(history["volume"].tail(lookback).mean())
        if avg_volume <= 0:
            return None

        close = float(df["close"].iloc[-1])
        volume = float(df["volume"].iloc[-1])
        volume_confirmed = volume >= avg_volume * float(self.params["volume_factor"])
        position = context.position_side(symbol)

        if position is None and volume_confirmed:
            if close > range_high:
                return self.make_signal(
                    SignalAction.BUY, symbol, df,
                    reason=f"Ausbruch über {range_high:.4f} mit {volume / avg_volume:.1f}x Volumen",
                    stop_loss=float(history["low"].tail(stop_lookback).min()),
                    range_high=range_high,
                )
            if close < range_low and self.params["allow_short"]:
                return self.make_signal(
                    SignalAction.SELL, symbol, df,
                    reason=f"Ausbruch unter {range_low:.4f} mit {volume / avg_volume:.1f}x Volumen",
                    stop_loss=float(history["high"].tail(stop_lookback).max()),
                    range_low=range_low,
                )
        # Exits: Rückfall in die Range.
        if position is PositionSide.LONG and close < range_high * 0.99:
            mid_range = (range_high + range_low) / 2.0
            if close < mid_range:
                return self.make_signal(
                    SignalAction.CLOSE_LONG, symbol, df, reason="Rückfall in die Range"
                )
        if position is PositionSide.SHORT and close > range_low * 1.01:
            mid_range = (range_high + range_low) / 2.0
            if close > mid_range:
                return self.make_signal(
                    SignalAction.CLOSE_SHORT, symbol, df, reason="Rückfall in die Range"
                )
        return None
