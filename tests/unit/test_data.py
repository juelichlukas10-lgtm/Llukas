"""Unit-Tests für das Daten-Modul (Storage, Downloader, Resampler, Stream)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from tests.mocks import MockExchangeAdapter
from tradingbot.core.enums import Timeframe
from tradingbot.core.events import EventBus, EventType
from tradingbot.core.exceptions import DataError
from tradingbot.data.downloader import HistoricalDownloader
from tradingbot.data.resampler import resample_candles
from tradingbot.data.storage import CandleStorage, candles_to_dataframe, dataframe_to_candles
from tradingbot.data.stream import MarketDataStream


@pytest.fixture()
def storage(tmp_path: Path) -> CandleStorage:
    return CandleStorage(tmp_path / "historical")


class TestStorage:
    def test_save_and_load_roundtrip(self, storage: CandleStorage, ohlcv_df: pd.DataFrame) -> None:
        storage.save(ohlcv_df, "binance", "BTC/USDT", Timeframe.M5)
        loaded = storage.load("binance", "BTC/USDT", Timeframe.M5)
        assert len(loaded) == len(ohlcv_df)
        pd.testing.assert_frame_equal(loaded, ohlcv_df.sort_index(), check_freq=False)

    def test_load_missing_returns_empty(self, storage: CandleStorage) -> None:
        df = storage.load("binance", "XRP/USDT", Timeframe.H1)
        assert df.empty

    def test_append_deduplicates(self, storage: CandleStorage, ohlcv_df: pd.DataFrame) -> None:
        first_half = ohlcv_df.iloc[:200]
        overlap = ohlcv_df.iloc[150:]
        storage.save(first_half, "binance", "BTC/USDT", Timeframe.M5)
        combined = storage.append(overlap, "binance", "BTC/USDT", Timeframe.M5)
        assert len(combined) == len(ohlcv_df)
        assert combined.index.is_unique
        assert combined.index.is_monotonic_increasing

    def test_load_with_time_range(self, storage: CandleStorage, ohlcv_df: pd.DataFrame) -> None:
        storage.save(ohlcv_df, "binance", "BTC/USDT", Timeframe.M5)
        start = ohlcv_df.index[100]
        end = ohlcv_df.index[199]
        subset = storage.load("binance", "BTC/USDT", Timeframe.M5, start=start, end=end)
        assert len(subset) == 100

    def test_last_timestamp(self, storage: CandleStorage, ohlcv_df: pd.DataFrame) -> None:
        assert storage.last_timestamp("binance", "BTC/USDT", Timeframe.M5) is None
        storage.save(ohlcv_df, "binance", "BTC/USDT", Timeframe.M5)
        assert storage.last_timestamp("binance", "BTC/USDT", Timeframe.M5) == ohlcv_df.index[-1]

    def test_list_datasets(self, storage: CandleStorage, ohlcv_df: pd.DataFrame) -> None:
        storage.save(ohlcv_df, "binance", "BTC/USDT", Timeframe.M5)
        storage.save(ohlcv_df, "bybit", "ETH/USDT", Timeframe.H1)
        datasets = storage.list_datasets()
        assert len(datasets) == 2
        exchanges = {d["exchange"] for d in datasets}
        assert exchanges == {"binance", "bybit"}

    def test_invalid_schema_rejected(self, storage: CandleStorage) -> None:
        bad = pd.DataFrame({"close": [1.0]}, index=pd.DatetimeIndex([datetime.now(timezone.utc)]))
        with pytest.raises(DataError, match="OHLCV"):
            storage.save(bad, "binance", "BTC/USDT", Timeframe.M5)

    def test_candle_conversion_roundtrip(self, ohlcv_df: pd.DataFrame) -> None:
        candles = dataframe_to_candles(ohlcv_df, "BTC/USDT", Timeframe.M5)
        df = candles_to_dataframe(candles)
        assert len(df) == len(ohlcv_df)
        assert df["close"].iloc[-1] == pytest.approx(ohlcv_df["close"].iloc[-1])


class TestResampler:
    def test_5m_to_15m(self, ohlcv_df: pd.DataFrame) -> None:
        result = resample_candles(ohlcv_df, Timeframe.M5, Timeframe.M15)
        assert len(result) == len(ohlcv_df) // 3
        first_bucket = ohlcv_df.iloc[:3]
        assert result["open"].iloc[0] == pytest.approx(first_bucket["open"].iloc[0])
        assert result["high"].iloc[0] == pytest.approx(first_bucket["high"].max())
        assert result["low"].iloc[0] == pytest.approx(first_bucket["low"].min())
        assert result["close"].iloc[0] == pytest.approx(first_bucket["close"].iloc[-1])
        assert result["volume"].iloc[0] == pytest.approx(first_bucket["volume"].sum())

    def test_incomplete_last_bucket_dropped(self, ohlcv_df: pd.DataFrame) -> None:
        df = ohlcv_df.iloc[:100]  # 100 Kerzen -> 33 volle 15m-Buckets + 1 Kerze Rest
        result = resample_candles(df, Timeframe.M5, Timeframe.M15)
        assert len(result) == 33

    def test_downsampling_rejected(self, ohlcv_df: pd.DataFrame) -> None:
        with pytest.raises(DataError, match="feiner"):
            resample_candles(ohlcv_df, Timeframe.M5, Timeframe.M1)

    def test_same_timeframe_returns_copy(self, ohlcv_df: pd.DataFrame) -> None:
        result = resample_candles(ohlcv_df, Timeframe.M5, Timeframe.M5)
        pd.testing.assert_frame_equal(result, ohlcv_df)
        assert result is not ohlcv_df


class TestDownloader:
    async def test_download_saves_data(self, storage: CandleStorage) -> None:
        exchange = MockExchangeAdapter(prices=[100.0 + i for i in range(50)])
        downloader = HistoricalDownloader(exchange, storage, batch_size=100)
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        df = await downloader.download("BTC/USDT", Timeframe.M5, start)
        assert not df.empty
        assert storage.exists("mock", "BTC/USDT", Timeframe.M5)

    async def test_invalid_range_raises(self, storage: CandleStorage) -> None:
        exchange = MockExchangeAdapter()
        downloader = HistoricalDownloader(exchange, storage)
        start = datetime(2024, 6, 1, tzinfo=timezone.utc)
        end = datetime(2024, 1, 1, tzinfo=timezone.utc)
        with pytest.raises(DataError, match="Ungültiger Zeitraum"):
            await downloader.download("BTC/USDT", Timeframe.M5, start, end)

    async def test_download_many_continues_on_error(self, storage: CandleStorage) -> None:
        exchange = MockExchangeAdapter(prices=[100.0 + i for i in range(20)])
        downloader = HistoricalDownloader(exchange, storage, batch_size=100)
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        results = await downloader.download_many(
            ["BTC/USDT", "ETH/USDT"], [Timeframe.M5], start
        )
        assert len(results) == 2


class TestMarketDataStream:
    async def test_bootstrap_and_stream(self) -> None:
        exchange = MockExchangeAdapter(prices=[100.0 + i for i in range(30)])
        bus = EventBus()
        candle_events: list[object] = []
        bus.subscribe(EventType.CANDLE, candle_events.append)

        stream = MarketDataStream(exchange, bus, history_size=100)
        await stream.start([("BTC/USDT", Timeframe.M5)])

        import asyncio

        await asyncio.sleep(0.2)  # Streams laufen lassen (Mock endet von selbst)
        await stream.stop()

        df = stream.get_candles("BTC/USDT", Timeframe.M5)
        assert not df.empty
        assert stream.last_price("BTC/USDT") is not None

    async def test_get_candles_empty_for_unknown(self) -> None:
        exchange = MockExchangeAdapter()
        stream = MarketDataStream(exchange, EventBus())
        df = stream.get_candles("UNKNOWN/USDT", Timeframe.M5)
        assert df.empty
