"""Kursdaten-Provider des Scanners (Yahoo Finance).

Kapselt ``yfinance`` hinter einer schlanken, asynchronen Schnittstelle:

    * Batch-Downloads (viele Ticker pro HTTP-Request)
    * TTL-Cache je Symbol (schont API und beschleunigt Scan-Zyklen)
    * Automatische Wiederholung fehlgeschlagener Ticker
    * Ausführung der blockierenden yfinance-Aufrufe in Threads,
      damit die asyncio-Loop (und damit die Hauptanwendung) nie blockiert

Andere Datenanbieter lassen sich über die ABC
:class:`StockDataProvider` ergänzen, ohne den Scanner zu ändern.
"""

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

import pandas as pd

from tradingbot.core.exceptions import DataError
from tradingbot.core.logging import get_logger

logger = get_logger(__name__)

OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]


class StockDataProvider(ABC):
    """Abstrakte Schnittstelle für Aktien-Kursdaten."""

    @abstractmethod
    async def fetch_history(
        self, symbols: list[str], period: str = "1y", interval: str = "1d"
    ) -> dict[str, pd.DataFrame]:
        """Lädt historische OHLCV-Daten für mehrere Symbole.

        Args:
            symbols: Tickersymbole.
            period: Zeitraum (z. B. ``"1y"``, ``"6mo"``).
            interval: Kerzen-Intervall (z. B. ``"1d"``, ``"1h"``).

        Returns:
            Mapping ``symbol -> OHLCV-DataFrame`` (Kleinbuchstaben-Spalten,
            DatetimeIndex). Symbole ohne Daten fehlen im Ergebnis.
        """

    @abstractmethod
    async def fetch_benchmark(self, period: str = "1y") -> pd.DataFrame:
        """Lädt die Benchmark-Historie (z. B. S&P-500-ETF) für relative Stärke."""


@dataclass(slots=True)
class _CacheEntry:
    df: pd.DataFrame
    fetched_at: float


class YFinanceProvider(StockDataProvider):
    """Yahoo-Finance-Provider mit Batching, Cache und Retry.

    Args:
        batch_size: Ticker pro Download-Request (yfinance bündelt intern).
        cache_ttl_seconds: Gültigkeitsdauer des Symbol-Caches.
        max_retries: Wiederholungen für fehlgeschlagene Ticker-Batches.
        benchmark_symbol: Symbol für die Marktbenchmark (Standard: SPY).
    """

    def __init__(
        self,
        batch_size: int = 100,
        cache_ttl_seconds: float = 600.0,
        max_retries: int = 2,
        benchmark_symbol: str = "SPY",
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size muss >= 1 sein")
        self._batch_size = batch_size
        self._cache_ttl = cache_ttl_seconds
        self._max_retries = max_retries
        self._benchmark_symbol = benchmark_symbol
        self._cache: dict[tuple[str, str, str], _CacheEntry] = {}
        self._cache_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Öffentliche API
    # ------------------------------------------------------------------

    async def fetch_history(
        self, symbols: list[str], period: str = "1y", interval: str = "1d"
    ) -> dict[str, pd.DataFrame]:
        result: dict[str, pd.DataFrame] = {}
        missing: list[str] = []

        async with self._cache_lock:
            now = time.monotonic()
            for symbol in symbols:
                entry = self._cache.get((symbol, period, interval))
                if entry is not None and now - entry.fetched_at < self._cache_ttl:
                    result[symbol] = entry.df
                else:
                    missing.append(symbol)

        if missing:
            fetched = await self._download_all(missing, period, interval)
            async with self._cache_lock:
                now = time.monotonic()
                for symbol, df in fetched.items():
                    self._cache[(symbol, period, interval)] = _CacheEntry(df, now)
            result.update(fetched)
        return result

    async def fetch_benchmark(self, period: str = "1y") -> pd.DataFrame:
        data = await self.fetch_history([self._benchmark_symbol], period=period)
        if self._benchmark_symbol not in data:
            raise DataError(f"Benchmark {self._benchmark_symbol} konnte nicht geladen werden")
        return data[self._benchmark_symbol]

    def clear_cache(self) -> None:
        """Leert den Symbol-Cache (z. B. für Tests)."""
        self._cache.clear()

    # ------------------------------------------------------------------
    # Interner Download
    # ------------------------------------------------------------------

    async def _download_all(
        self, symbols: list[str], period: str, interval: str
    ) -> dict[str, pd.DataFrame]:
        """Lädt Symbole in Batches; wiederholt fehlgeschlagene Ticker."""
        result: dict[str, pd.DataFrame] = {}
        remaining = list(dict.fromkeys(symbols))

        for attempt in range(self._max_retries + 1):
            if not remaining:
                break
            failed: list[str] = []
            for start in range(0, len(remaining), self._batch_size):
                batch = remaining[start : start + self._batch_size]
                try:
                    fetched = await asyncio.to_thread(
                        self._download_batch_sync, batch, period, interval
                    )
                except Exception:
                    logger.exception("Batch-Download fehlgeschlagen (%d Ticker)", len(batch))
                    failed.extend(batch)
                    continue
                result.update(fetched)
                failed.extend(s for s in batch if s not in fetched)

            remaining = failed
            if remaining and attempt < self._max_retries:
                delay = 2.0 * (attempt + 1)
                logger.info(
                    "%d Ticker ohne Daten – neuer Versuch in %.0fs (Versuch %d/%d)",
                    len(remaining), delay, attempt + 1, self._max_retries,
                )
                await asyncio.sleep(delay)

        if remaining:
            logger.debug("Keine Daten für %d Ticker (übersprungen): %s",
                         len(remaining), remaining[:15])
        return result

    @staticmethod
    def _download_batch_sync(
        symbols: list[str], period: str, interval: str
    ) -> dict[str, pd.DataFrame]:
        """Blockierender yfinance-Batch-Download (läuft im Thread-Executor)."""
        import yfinance as yf

        raw = yf.download(
            symbols,
            period=period,
            interval=interval,
            group_by="ticker",
            auto_adjust=True,
            progress=False,
            threads=True,
        )
        result: dict[str, pd.DataFrame] = {}
        if raw is None or raw.empty:
            return result

        if len(symbols) == 1 and raw.columns.nlevels == 1:
            frames: dict[str, pd.DataFrame] = {symbols[0]: raw}
        else:
            frames = {
                symbol: raw[symbol]
                for symbol in symbols
                if symbol in raw.columns.get_level_values(0)
            }

        for symbol, df in frames.items():
            cleaned = _normalize_ohlcv(df)
            if len(cleaned) >= 30:  # Mindesthistorie, sonst unbrauchbar
                result[symbol] = cleaned
        return result


def _normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """Vereinheitlicht ein yfinance-Frame auf das Scanner-Schema."""
    renamed = df.rename(columns={c: c.lower() for c in df.columns})
    keep = [c for c in OHLCV_COLUMNS if c in renamed.columns]
    cleaned = renamed[keep].dropna(how="any")
    cleaned.index = pd.DatetimeIndex(cleaned.index)
    if cleaned.index.tz is None:
        cleaned.index = cleaned.index.tz_localize("UTC")
    else:
        cleaned.index = cleaned.index.tz_convert("UTC")
    return cleaned.sort_index()
