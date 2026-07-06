"""Donchian-Channel-Strategie (Turtle-Trading-Prinzip).

Kauft beim Ausbruch über das höchste Hoch der letzten N Kerzen und
schließt beim Bruch des Exit-Kanals (kürzere Periode).
"""

from __future__ import annotations

import pandas as pd

from tradingbot.analytics.indicators import donchian_channel
from tradingbot.core.enums import PositionSide, SignalAction
from tradingbot.core.exceptions import StrategyError
from tradingbot.core.models import Signal
from tradingbot.strategies.base import Strategy, StrategyContext
from tradingbot.strategies.registry import register_strategy


@register_strategy("donchian")
class DonchianStrategy(Strategy):
    """Kanal-Ausbruchsstrategie nach Donchian.

    Params:
        entry_period: Periode des Einstiegskanals (Standard 20).
        exit_period: Periode des Exit-Kanals (Standard 10).
        allow_short: Short-Ausbrüche handeln (Standard False).
    """

    default_params = {"entry_period": 20, "exit_period": 10, "allow_short": False}

    def _validate_params(self) -> None:
        super()._validate_params()
        if self.params["exit_period"] >= self.params["entry_period"]:
            raise StrategyError(f"{self.name}: exit_period muss kleiner als entry_period sein")

    def generate_signal(
        self, df: pd.DataFrame, symbol: str, context: StrategyContext
    ) -> Signal | None:
        if not self.has_enough_history(df):
            return None

        # Kanäle ohne aktuelle Kerze berechnen, damit der Ausbruch messbar ist.
        entry_channel = donchian_channel(df.iloc[:-1], int(self.params["entry_period"]))
        exit_channel = donchian_channel(df.iloc[:-1], int(self.params["exit_period"]))
        if entry_channel.dropna().empty or exit_channel.dropna().empty:
            return None

        close = float(df["close"].iloc[-1])
        entry_upper = float(entry_channel["upper"].iloc[-1])
        entry_lower = float(entry_channel["lower"].iloc[-1])
        exit_upper = float(exit_channel["upper"].iloc[-1])
        exit_lower = float(exit_channel["lower"].iloc[-1])
        position = context.position_side(symbol)

        if position is None and close > entry_upper:
            return self.make_signal(
                SignalAction.BUY, symbol, df,
                reason=f"Donchian-Ausbruch über {entry_upper:.4f}",
                stop_loss=exit_lower,
            )
        if position is PositionSide.LONG and close < exit_lower:
            return self.make_signal(
                SignalAction.CLOSE_LONG, symbol, df, reason=f"Exit-Kanal gebrochen ({exit_lower:.4f})"
            )
        if self.params["allow_short"]:
            if position is None and close < entry_lower:
                return self.make_signal(
                    SignalAction.SELL, symbol, df,
                    reason=f"Donchian-Ausbruch unter {entry_lower:.4f}",
                    stop_loss=exit_upper,
                )
            if position is PositionSide.SHORT and close > exit_upper:
                return self.make_signal(
                    SignalAction.CLOSE_SHORT, symbol, df, reason="Exit-Kanal gebrochen"
                )
        return None
