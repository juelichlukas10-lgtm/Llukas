"""DCA-Strategie (Dollar Cost Averaging mit Dip-Filter).

Kauft in festen Kerzen-Intervallen, bevorzugt bei Kursrückgängen
(Dip-Bonus). Verkauft optional bei Erreichen eines Gewinnziels über
dem Durchschnittseinstand.
"""

from __future__ import annotations

import pandas as pd

from tradingbot.core.enums import PositionSide, SignalAction
from tradingbot.core.exceptions import StrategyError
from tradingbot.core.models import Signal
from tradingbot.strategies.base import Strategy, StrategyContext
from tradingbot.strategies.registry import register_strategy


@register_strategy("dca")
class DcaStrategy(Strategy):
    """Intervallbasiertes Akkumulieren mit Dip-Verstärkung.

    Params:
        interval_candles: Kerzen zwischen zwei Käufen (Standard 288 =
            1 Tag auf 5m).
        dip_threshold: Kursrückgang seit letztem Kauf, der einen
            sofortigen Zusatzkauf auslöst (Standard 0.05 = 5 %).
        take_profit_pct: Gewinnziel über dem letzten Kaufpreis für einen
            Teil-Exit; 0 deaktiviert Verkäufe (Standard 0.0 = reines HODL).
    """

    default_params = {"interval_candles": 288, "dip_threshold": 0.05, "take_profit_pct": 0.0}

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self._candles_since_buy: dict[str, int] = {}
        self._last_buy_price: dict[str, float] = {}

    def _validate_params(self) -> None:
        super()._validate_params()
        if not isinstance(self.params["interval_candles"], int) or self.params["interval_candles"] < 1:
            raise StrategyError(f"{self.name}: interval_candles muss ein positiver Integer sein")

    @property
    def required_history(self) -> int:
        return 2

    def generate_signal(
        self, df: pd.DataFrame, symbol: str, context: StrategyContext
    ) -> Signal | None:
        if len(df) < self.required_history:
            return None

        price = float(df["close"].iloc[-1])
        counter = self._candles_since_buy.get(symbol, int(self.params["interval_candles"]))
        counter += 1
        self._candles_since_buy[symbol] = counter

        last_buy = self._last_buy_price.get(symbol)
        take_profit = float(self.params["take_profit_pct"])
        position = context.position_side(symbol)

        # Optionaler Teil-Exit bei erreichtem Gewinnziel.
        if (
            take_profit > 0
            and position is PositionSide.LONG
            and last_buy is not None
            and price >= last_buy * (1.0 + take_profit)
        ):
            return self.make_signal(
                SignalAction.CLOSE_LONG, symbol, df,
                reason=f"DCA-Gewinnziel {take_profit:.1%} erreicht",
            )

        interval_due = counter >= int(self.params["interval_candles"])
        dip = (
            last_buy is not None
            and price <= last_buy * (1.0 - float(self.params["dip_threshold"]))
        )

        if interval_due or dip:
            self._candles_since_buy[symbol] = 0
            self._last_buy_price[symbol] = price
            reason = "DCA-Dip-Kauf" if dip else "DCA-Intervallkauf"
            return self.make_signal(
                SignalAction.BUY, symbol, df,
                reason=reason,
                confidence=1.0 if dip else 0.5,
                dip=dip,
            )
        return None
