"""Unit-Tests für die Indikator-Bibliothek."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tradingbot.analytics import indicators as ind


class TestMovingAverages:
    def test_sma_known_values(self) -> None:
        series = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        result = ind.sma(series, 3)
        assert np.isnan(result.iloc[0]) and np.isnan(result.iloc[1])
        assert result.iloc[2] == pytest.approx(2.0)
        assert result.iloc[4] == pytest.approx(4.0)

    def test_ema_converges_to_constant(self) -> None:
        series = pd.Series([50.0] * 100)
        result = ind.ema(series, 10)
        assert result.iloc[-1] == pytest.approx(50.0)

    def test_invalid_period_raises(self) -> None:
        with pytest.raises(ValueError):
            ind.sma(pd.Series([1.0, 2.0]), 0)
        with pytest.raises(ValueError):
            ind.ema(pd.Series([1.0, 2.0]), -3)


class TestRSI:
    def test_bounds(self, ohlcv_df: pd.DataFrame) -> None:
        result = ind.rsi(ohlcv_df["close"], 14).dropna()
        assert ((result >= 0) & (result <= 100)).all()

    def test_all_gains_is_100(self) -> None:
        series = pd.Series(np.arange(1.0, 40.0))
        result = ind.rsi(series, 14)
        assert result.iloc[-1] == pytest.approx(100.0)

    def test_all_losses_is_0(self) -> None:
        series = pd.Series(np.arange(40.0, 1.0, -1.0))
        result = ind.rsi(series, 14)
        assert result.iloc[-1] == pytest.approx(0.0, abs=1e-9)


class TestMACD:
    def test_columns_and_histogram(self, ohlcv_df: pd.DataFrame) -> None:
        result = ind.macd(ohlcv_df["close"])
        assert list(result.columns) == ["macd", "signal", "histogram"]
        valid = result.dropna()
        assert not valid.empty
        expected = valid["macd"] - valid["signal"]
        pd.testing.assert_series_equal(valid["histogram"], expected, check_names=False)


class TestVolatility:
    def test_atr_positive(self, ohlcv_df: pd.DataFrame) -> None:
        result = ind.atr(ohlcv_df, 14).dropna()
        assert (result > 0).all()

    def test_bollinger_order(self, ohlcv_df: pd.DataFrame) -> None:
        bb = ind.bollinger_bands(ohlcv_df["close"]).dropna()
        assert (bb["upper"] >= bb["middle"]).all()
        assert (bb["middle"] >= bb["lower"]).all()

    def test_keltner_order(self, ohlcv_df: pd.DataFrame) -> None:
        kc = ind.keltner_channel(ohlcv_df).dropna()
        assert (kc["upper"] >= kc["middle"]).all()
        assert (kc["middle"] >= kc["lower"]).all()

    def test_donchian_contains_close(self, ohlcv_df: pd.DataFrame) -> None:
        dc = ind.donchian_channel(ohlcv_df, 20).dropna()
        closes = ohlcv_df["close"].loc[dc.index]
        assert (closes <= dc["upper"] + 1e-9).all()
        assert (closes >= dc["lower"] - 1e-9).all()


class TestTrend:
    def test_adx_bounds(self, ohlcv_df: pd.DataFrame) -> None:
        result = ind.adx(ohlcv_df, 14).dropna()
        assert ((result["adx"] >= 0) & (result["adx"] <= 100)).all()
        assert (result["plus_di"] >= 0).all()
        assert (result["minus_di"] >= 0).all()

    def test_supertrend_direction_values(self, ohlcv_df: pd.DataFrame) -> None:
        st = ind.supertrend(ohlcv_df)
        directions = set(st["direction"].dropna().unique()) - {0.0}
        assert directions.issubset({1.0, -1.0})

    def test_supertrend_uptrend_detection(self) -> None:
        n = 100
        close = pd.Series(np.linspace(100, 200, n))
        df = pd.DataFrame(
            {"open": close, "high": close + 1, "low": close - 1, "close": close, "volume": 1.0}
        )
        st = ind.supertrend(df)
        assert st["direction"].iloc[-1] == 1.0
        assert st["supertrend"].iloc[-1] < close.iloc[-1]

    def test_parabolic_sar_below_price_in_uptrend(self) -> None:
        n = 80
        close = pd.Series(np.linspace(100, 150, n))
        df = pd.DataFrame(
            {"open": close, "high": close + 0.5, "low": close - 0.5, "close": close, "volume": 1.0}
        )
        sar = ind.parabolic_sar(df)
        assert sar.iloc[-1] < close.iloc[-1]

    def test_ichimoku_columns(self, ohlcv_df: pd.DataFrame) -> None:
        result = ind.ichimoku(ohlcv_df)
        assert list(result.columns) == ["tenkan", "kijun", "senkou_a", "senkou_b", "chikou"]
        assert not result["tenkan"].dropna().empty


class TestVolume:
    def test_vwap_within_price_range(self, ohlcv_df: pd.DataFrame) -> None:
        result = ind.vwap(ohlcv_df).dropna()
        assert (result >= ohlcv_df["low"].min() - 1e-9).all()
        assert (result <= ohlcv_df["high"].max() + 1e-9).all()

    def test_obv_direction(self) -> None:
        df = pd.DataFrame(
            {
                "open": [1, 1, 1, 1],
                "high": [1, 2, 3, 4],
                "low": [1, 1, 1, 1],
                "close": [1.0, 2.0, 3.0, 2.0],
                "volume": [10.0, 10.0, 10.0, 10.0],
            }
        )
        result = ind.obv(df)
        assert result.iloc[1] == pytest.approx(10.0)
        assert result.iloc[2] == pytest.approx(20.0)
        assert result.iloc[3] == pytest.approx(10.0)

    def test_mfi_bounds(self, ohlcv_df: pd.DataFrame) -> None:
        result = ind.mfi(ohlcv_df, 14).dropna()
        assert ((result >= 0) & (result <= 100)).all()

    def test_volume_profile_poc(self, ohlcv_df: pd.DataFrame) -> None:
        profile = ind.volume_profile(ohlcv_df, bins=10)
        assert not profile.empty
        assert profile["volume"].iloc[0] == profile["volume"].max()
        assert profile["volume"].sum() == pytest.approx(ohlcv_df["volume"].sum())


class TestPivotsAndCrosses:
    def test_pivot_levels_order(self, ohlcv_df: pd.DataFrame) -> None:
        pp = ind.pivot_points(ohlcv_df).dropna()
        assert (pp["r3"] >= pp["r2"]).all()
        assert (pp["r2"] >= pp["r1"]).all()
        assert (pp["s1"] >= pp["s2"]).all()
        assert (pp["s2"] >= pp["s3"]).all()

    def test_crossover_and_crossunder(self) -> None:
        fast = pd.Series([1.0, 2.0, 3.0, 2.0, 1.0])
        slow = pd.Series([2.0, 2.0, 2.0, 2.0, 2.0])
        up = ind.crossover(fast, slow)
        down = ind.crossunder(fast, slow)
        assert up.tolist() == [False, False, True, False, False]
        assert down.tolist() == [False, False, False, False, True]

    def test_cci_centered(self, ohlcv_df: pd.DataFrame) -> None:
        result = ind.cci(ohlcv_df, 20).dropna()
        assert result.abs().max() < 1000

    def test_stochastic_bounds(self, ohlcv_df: pd.DataFrame) -> None:
        result = ind.stochastic(ohlcv_df).dropna()
        assert ((result["k"] >= 0) & (result["k"] <= 100)).all()
        assert ((result["d"] >= 0) & (result["d"] <= 100)).all()
