"""Mean-Reversion-Strategie auf Z-Score-Basis.

Kauft, wenn der Kurs statistisch signifikant (Z-Score) unter seinem
gleitenden Mittel notiert, und schließt bei Rückkehr zum Mittelwert.
"""

from __future__ import annotations

import pandas as pd

from tradingbot.analytics.indicators import sma
from tradingbot.core.enums import PositionSide, SignalAction
from tradingbot.core.exceptions import StrategyError
from tradingbot.core.models import Signal
from tradingbot.strategies.base import Strategy, StrategyContext
from tradingbot.strategies.registry import register_strategy


@register_strategy("mean_reversion")
class MeanReversionStrategy(Strategy):
    """Statistische Rückkehr zum Mittelwert.

    Params:
        lookback_period: Fenster für Mittelwert/Standardabweichung (Standard 20).
        entry_z: Z-Score-Schwelle für den Einstieg (Standard 2.0).
        exit_z: Z-Score-Schwelle für den Ausstieg (Standard 0.5).
        allow_short: Short bei positiver Abweichung (Standard False).
    """

    default_params = {"lookback_period": 20, "entry_z": 2.0, "exit_z": 0.5, "allow_short": False}

    def _validate_params(self) -> None:
        super()._validate_params()
        if self.params["exit_z"] >= self.params["entry_z"]:
            raise StrategyError(f"{self.name}: exit_z muss kleiner als entry_z sein")

    def generate_signal(
        self, df: pd.DataFrame, symbol: str, context: StrategyContext
    ) -> Signal | None:
        if not self.has_enough_history(df):
            return None

        period = int(self.params["lookback_period"])
        mean = sma(df["close"], period)
        std = df["close"].rolling(period, min_periods=period).std(ddof=0)
        current_std = float(std.iloc[-1])
        if pd.isna(current_std) or current_std <= 0:
            return None

        z = (float(df["close"].iloc[-1]) - float(mean.iloc[-1])) / current_std
        position = context.position_side(symbol)
        entry_z, exit_z = float(self.params["entry_z"]), float(self.params["exit_z"])

        if position is None:
            if z <= -entry_z:
                return self.make_signal(
                    SignalAction.BUY, symbol, df,
                    reason=f"Z-Score {z:.2f} <= -{entry_z}",
                    confidence=min(1.0, abs(z) / (entry_z * 2)),
                    take_profit=float(mean.iloc[-1]),
                    z_score=z,
                )
            if z >= entry_z and self.params["allow_short"]:
                return self.make_signal(
                    SignalAction.SELL, symbol, df,
                    reason=f"Z-Score {z:.2f} >= {entry_z}",
                    take_profit=float(mean.iloc[-1]),
                    z_score=z,
                )
        elif position is PositionSide.LONG and z >= -exit_z:
            return self.make_signal(
                SignalAction.CLOSE_LONG, symbol, df, reason=f"Z-Score normalisiert ({z:.2f})"
            )
        elif position is PositionSide.SHORT and z <= exit_z:
            return self.make_signal(
                SignalAction.CLOSE_SHORT, symbol, df, reason=f"Z-Score normalisiert ({z:.2f})"
            )
        return None
