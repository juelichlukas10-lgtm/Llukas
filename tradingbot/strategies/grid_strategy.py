"""Grid-Trading-Strategie.

Spannt ein Preisraster um einen Ankerpreis. Fällt der Kurs auf ein
tieferes Grid-Level, wird gekauft; steigt er auf ein höheres Level,
wird (teil-)verkauft. Die Strategie hält ihren Zustand (Anker, letztes
Level) über Aufrufe hinweg.
"""

from __future__ import annotations

import pandas as pd

from tradingbot.core.enums import PositionSide, SignalAction
from tradingbot.core.exceptions import StrategyError
from tradingbot.core.models import Signal
from tradingbot.strategies.base import Strategy, StrategyContext
from tradingbot.strategies.registry import register_strategy


@register_strategy("grid")
class GridStrategy(Strategy):
    """Symmetrisches Preisraster um einen dynamischen Anker.

    Params:
        grid_spacing: Abstand zwischen Grid-Levels als Bruchteil
            (Standard 0.01 = 1 %).
        grid_levels: Anzahl Levels ober-/unterhalb des Ankers (Standard 5).
        reset_threshold: Kursabstand vom Anker, ab dem das Grid neu
            zentriert wird (Standard 0.12 = 12 %).
    """

    default_params = {"grid_spacing": 0.01, "grid_levels": 5, "reset_threshold": 0.12}

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self._anchor: dict[str, float] = {}
        self._last_level: dict[str, int] = {}

    def _validate_params(self) -> None:
        super()._validate_params()
        if self.params["grid_spacing"] <= 0:
            raise StrategyError(f"{self.name}: grid_spacing muss > 0 sein")
        if not isinstance(self.params["grid_levels"], int) or self.params["grid_levels"] < 1:
            raise StrategyError(f"{self.name}: grid_levels muss ein positiver Integer sein")

    @property
    def required_history(self) -> int:
        return 2

    def _current_level(self, symbol: str, price: float) -> int:
        """Grid-Level des Preises relativ zum Anker (0 = am Anker)."""
        anchor = self._anchor[symbol]
        spacing = float(self.params["grid_spacing"])
        return round((price - anchor) / (anchor * spacing))

    def generate_signal(
        self, df: pd.DataFrame, symbol: str, context: StrategyContext
    ) -> Signal | None:
        if len(df) < self.required_history:
            return None

        price = float(df["close"].iloc[-1])
        max_levels = int(self.params["grid_levels"])

        # Grid initialisieren oder bei zu großem Abstand neu zentrieren.
        if symbol not in self._anchor or (
            abs(price - self._anchor[symbol]) / self._anchor[symbol]
            > float(self.params["reset_threshold"])
        ):
            self._anchor[symbol] = price
            self._last_level[symbol] = 0
            return None

        level = max(-max_levels, min(max_levels, self._current_level(symbol, price)))
        last_level = self._last_level[symbol]
        if level == last_level:
            return None

        self._last_level[symbol] = level
        position = context.position_side(symbol)

        if level < last_level and position is not PositionSide.SHORT:
            # Kurs ist auf ein tieferes Level gefallen -> kaufen.
            return self.make_signal(
                SignalAction.BUY, symbol, df,
                reason=f"Grid-Kauf auf Level {level} (Anker {self._anchor[symbol]:.4f})",
                confidence=min(1.0, abs(level) / max_levels),
                grid_level=level,
            )
        if level > last_level and position is PositionSide.LONG:
            # Kurs ist auf ein höheres Level gestiegen -> Gewinn realisieren.
            return self.make_signal(
                SignalAction.CLOSE_LONG, symbol, df,
                reason=f"Grid-Verkauf auf Level {level}",
                grid_level=level,
            )
        return None
