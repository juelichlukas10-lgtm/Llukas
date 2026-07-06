"""Unit-Tests für das Strategie-Plugin-System und alle Strategien."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tradingbot.core.enums import PositionSide, SignalAction, Timeframe
from tradingbot.core.exceptions import StrategyError
from tradingbot.core.models import Signal
from tradingbot.strategies import (
    Strategy,
    StrategyContext,
    create_strategy,
    discover_strategies,
    list_strategies,
    register_strategy,
)

EXPECTED_STRATEGIES = {
    "ema_crossover",
    "rsi",
    "macd",
    "bollinger",
    "vwap",
    "supertrend",
    "donchian",
    "atr_breakout",
    "mean_reversion",
    "momentum",
    "trend_following",
    "breakout",
    "scalping",
    "grid",
    "dca",
    "mtf_confirmation",
    "volume",
    "order_flow",
}


def _make_df(prices: list[float], volumes: list[float] | None = None) -> pd.DataFrame:
    """Baut einen OHLCV-DataFrame aus einer Schlusskursliste."""
    n = len(prices)
    closes = np.asarray(prices, dtype=float)
    opens = np.concatenate(([closes[0]], closes[:-1]))
    highs = np.maximum(opens, closes) * 1.001
    lows = np.minimum(opens, closes) * 0.999
    vols = np.asarray(volumes, dtype=float) if volumes else np.full(n, 100.0)
    index = pd.date_range("2024-01-01", periods=n, freq="5min", tz="UTC")
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": vols}, index=index
    )


class TestRegistry:
    def test_all_strategies_discovered(self) -> None:
        registry = discover_strategies()
        assert EXPECTED_STRATEGIES.issubset(set(registry))

    def test_list_strategies_sorted(self) -> None:
        names = list_strategies()
        assert names == sorted(names)
        assert len(names) >= 18

    def test_create_strategy(self) -> None:
        strategy = create_strategy(
            "ema_crossover", ["BTC/USDT"], Timeframe.M5, {"fast_period": 5, "slow_period": 20}
        )
        assert strategy.name == "ema_crossover"
        assert strategy.params["fast_period"] == 5
        assert strategy.params["allow_short"] is False  # Default erhalten

    def test_unknown_strategy_raises(self) -> None:
        with pytest.raises(StrategyError, match="Unbekannte Strategie"):
            create_strategy("does_not_exist", ["BTC/USDT"])

    def test_duplicate_name_rejected(self) -> None:
        from tradingbot.strategies.ema_crossover import EmaCrossoverStrategy  # noqa: F401

        with pytest.raises(StrategyError, match="bereits registriert"):

            @register_strategy("ema_crossover")
            class Clone(Strategy):
                def generate_signal(self, df, symbol, context):  # type: ignore[override]
                    return None

    def test_no_symbols_rejected(self) -> None:
        with pytest.raises(StrategyError, match="Symbol"):
            create_strategy("rsi", [])


class TestAllStrategiesSmoke:
    """Jede Strategie muss auf beliebigen Daten robust laufen."""

    @pytest.mark.parametrize("name", sorted(EXPECTED_STRATEGIES))
    def test_returns_signal_or_none(self, name: str, ohlcv_df: pd.DataFrame) -> None:
        strategy = create_strategy(name, ["BTC/USDT"], Timeframe.M5)
        context = StrategyContext(
            get_candles=lambda symbol, tf, df=ohlcv_df: df,  # HTF-Daten für MTF-Strategien
        )
        result = strategy.generate_signal(ohlcv_df, "BTC/USDT", context)
        assert result is None or isinstance(result, Signal)
        if result is not None:
            assert result.symbol == "BTC/USDT"
            assert result.strategy == name
            assert result.price > 0

    @pytest.mark.parametrize("name", sorted(EXPECTED_STRATEGIES))
    def test_handles_short_history(self, name: str, ohlcv_df: pd.DataFrame) -> None:
        strategy = create_strategy(name, ["BTC/USDT"], Timeframe.M5)
        tiny = ohlcv_df.iloc[:3]
        result = strategy.generate_signal(tiny, "BTC/USDT", StrategyContext())
        assert result is None or isinstance(result, Signal)


class TestEmaCrossover:
    def test_golden_cross_generates_buy(self) -> None:
        # 80 fallende Kerzen, dann steiler Anstieg -> schnelles EMA kreuzt nach oben.
        prices = list(np.linspace(120, 100, 80)) + list(np.linspace(100, 130, 20))
        df = _make_df(prices)
        strategy = create_strategy(
            "ema_crossover", ["BTC/USDT"], params={"fast_period": 5, "slow_period": 20}
        )
        signals = []
        for i in range(60, len(df)):
            signal = strategy.generate_signal(df.iloc[: i + 1], "BTC/USDT", StrategyContext())
            if signal is not None:
                signals.append(signal)
        assert any(s.action is SignalAction.BUY for s in signals)

    def test_close_long_on_death_cross(self) -> None:
        prices = list(np.linspace(100, 130, 80)) + list(np.linspace(130, 100, 20))
        df = _make_df(prices)
        strategy = create_strategy(
            "ema_crossover", ["BTC/USDT"], params={"fast_period": 5, "slow_period": 20}
        )
        context = StrategyContext(position_side=lambda s: PositionSide.LONG)
        signals = []
        for i in range(60, len(df)):
            signal = strategy.generate_signal(df.iloc[: i + 1], "BTC/USDT", context)
            if signal is not None:
                signals.append(signal)
        assert any(s.action is SignalAction.CLOSE_LONG for s in signals)

    def test_invalid_periods_rejected(self) -> None:
        with pytest.raises(StrategyError, match="fast_period"):
            create_strategy(
                "ema_crossover", ["BTC/USDT"], params={"fast_period": 30, "slow_period": 10}
            )


class TestRsiStrategy:
    def test_buy_after_oversold_recovery(self) -> None:
        # Starker Abverkauf, dann Erholung -> RSI dreht aus überverkaufter Zone.
        prices = list(np.linspace(100, 70, 60)) + list(np.linspace(70, 80, 15))
        df = _make_df(prices)
        strategy = create_strategy("rsi", ["BTC/USDT"], params={"period": 14})
        signals = []
        for i in range(45, len(df)):
            signal = strategy.generate_signal(df.iloc[: i + 1], "BTC/USDT", StrategyContext())
            if signal is not None:
                signals.append(signal)
        assert any(s.action is SignalAction.BUY for s in signals)

    def test_invalid_thresholds(self) -> None:
        with pytest.raises(StrategyError):
            create_strategy("rsi", ["BTC/USDT"], params={"oversold": 80.0, "overbought": 20.0})


class TestGridStrategy:
    def test_buy_on_lower_level_and_sell_on_recovery(self) -> None:
        strategy = create_strategy(
            "grid", ["BTC/USDT"], params={"grid_spacing": 0.01, "grid_levels": 5}
        )
        base = _make_df([100.0, 100.0])
        # Anker setzen.
        assert strategy.generate_signal(base, "BTC/USDT", StrategyContext()) is None

        # Kurs fällt 2 % -> Grid-Kauf.
        dip = _make_df([100.0, 100.0, 98.0])
        signal = strategy.generate_signal(dip, "BTC/USDT", StrategyContext())
        assert signal is not None and signal.action is SignalAction.BUY

        # Kurs erholt sich auf Anker -> Verkauf des Grid-Kaufs.
        recovery = _make_df([100.0, 100.0, 98.0, 100.0])
        context = StrategyContext(position_side=lambda s: PositionSide.LONG)
        signal = strategy.generate_signal(recovery, "BTC/USDT", context)
        assert signal is not None and signal.action is SignalAction.CLOSE_LONG


class TestDcaStrategy:
    def test_interval_buying(self) -> None:
        strategy = create_strategy("dca", ["BTC/USDT"], params={"interval_candles": 5})
        df = _make_df([100.0] * 30)
        buys = 0
        for i in range(2, len(df)):
            signal = strategy.generate_signal(df.iloc[: i + 1], "BTC/USDT", StrategyContext())
            if signal is not None and signal.action is SignalAction.BUY:
                buys += 1
        assert buys >= 4  # ~alle 5 Kerzen ein Kauf über 28 Auswertungen

    def test_dip_triggers_extra_buy(self) -> None:
        strategy = create_strategy(
            "dca", ["BTC/USDT"], params={"interval_candles": 1000, "dip_threshold": 0.05}
        )
        # Erster Kauf (Intervall beim ersten Aufruf fällig).
        df1 = _make_df([100.0, 100.0])
        signal = strategy.generate_signal(df1, "BTC/USDT", StrategyContext())
        assert signal is not None and signal.action is SignalAction.BUY

        # 6 % Dip -> Zusatzkauf trotz nicht abgelaufenem Intervall.
        df2 = _make_df([100.0, 100.0, 94.0])
        signal = strategy.generate_signal(df2, "BTC/USDT", StrategyContext())
        assert signal is not None and signal.action is SignalAction.BUY
        assert signal.metadata.get("dip") is True


class TestMtfConfirmation:
    def test_requires_coarser_higher_timeframe(self) -> None:
        with pytest.raises(StrategyError, match="gröber"):
            create_strategy(
                "mtf_confirmation", ["BTC/USDT"], Timeframe.H4, params={"higher_timeframe": "1h"}
            )

    def test_no_signal_without_htf_data(self, ohlcv_df: pd.DataFrame) -> None:
        strategy = create_strategy("mtf_confirmation", ["BTC/USDT"], Timeframe.M5)
        context = StrategyContext()  # get_candles liefert leeren DataFrame
        assert strategy.generate_signal(ohlcv_df, "BTC/USDT", context) is None

    def test_additional_timeframes_exposed(self) -> None:
        strategy = create_strategy(
            "mtf_confirmation", ["BTC/USDT"], Timeframe.M5, params={"higher_timeframe": "4h"}
        )
        assert strategy.additional_timeframes == [Timeframe.H4]


class TestOrderFlow:
    def test_uses_order_book_when_available(self, ohlcv_df: pd.DataFrame) -> None:
        from datetime import datetime, timezone

        from tradingbot.core.models import OrderBook, OrderBookLevel

        heavy_bids = OrderBook(
            symbol="BTC/USDT",
            timestamp=datetime.now(timezone.utc),
            bids=tuple(OrderBookLevel(100.0 - i, 10.0) for i in range(10)),
            asks=tuple(OrderBookLevel(100.0 + i, 1.0) for i in range(10)),
        )
        strategy = create_strategy("order_flow", ["BTC/USDT"], params={"imbalance_threshold": 0.3})
        context = StrategyContext(get_order_book=lambda s: heavy_bids)
        signal = strategy.generate_signal(ohlcv_df, "BTC/USDT", context)
        assert signal is not None
        assert signal.action is SignalAction.BUY
        assert signal.metadata["source"] == "orderbook"
