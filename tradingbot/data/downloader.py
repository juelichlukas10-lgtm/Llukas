"""Automatischer Download historischer Kerzendaten.

Der :class:`HistoricalDownloader` lädt OHLCV-Daten seitenweise über die
REST-API einer Börse, setzt vorhandene lokale Bestände fort
(inkrementeller Download) und speichert alles im Parquet-Format.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from tradingbot.core.enums import Timeframe
from tradingbot.core.exceptions import DataError
from tradingbot.core.logging import get_logger
from tradingbot.data.storage import CandleStorage, candles_to_dataframe
from tradingbot.exchange.base import ExchangeAdapter, retry_async

logger = get_logger(__name__)


class HistoricalDownloader:
    """Lädt historische Kerzen einer Börse und speichert sie lokal.

    Args:
        exchange: Verbundener Exchange-Adapter (öffentliche Endpunkte genügen).
        storage: Ziel-Speicher für die Daten.
        batch_size: Kerzen pro REST-Request (börsenabhängig, max. ~1000-1500).
    """

    def __init__(
        self,
        exchange: ExchangeAdapter,
        storage: CandleStorage,
        batch_size: int = 1000,
    ) -> None:
        if batch_size < 10:
            raise ValueError("batch_size muss mindestens 10 sein")
        self._exchange = exchange
        self._storage = storage
        self._batch_size = batch_size

    async def download(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime | None = None,
        incremental: bool = True,
    ) -> pd.DataFrame:
        """Lädt Kerzen für einen Zeitraum herunter und speichert sie.

        Bereits lokal vorhandene Daten werden bei ``incremental=True``
        fortgesetzt statt erneut geladen.

        Args:
            symbol: Handelspaar, z. B. ``"BTC/USDT"``.
            timeframe: Kerzen-Timeframe.
            start: Startzeitpunkt (UTC).
            end: Endzeitpunkt (UTC); None = jetzt.
            incremental: Vorhandene lokale Daten fortsetzen.

        Returns:
            Vollständiger lokaler Bestand für den Zeitraum als DataFrame.

        Raises:
            DataError: Bei ungültigem Zeitraum.
        """
        end = end or datetime.now(timezone.utc)
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        if start >= end:
            raise DataError(f"Ungültiger Zeitraum: start={start} >= end={end}")

        effective_start = start
        if incremental:
            last = self._storage.last_timestamp(self._exchange.name, symbol, timeframe)
            if last is not None and last.to_pydatetime() >= start:
                effective_start = last.to_pydatetime() + pd.Timedelta(
                    seconds=timeframe.seconds
                )
                logger.info(
                    "%s %s %s: lokale Daten bis %s vorhanden, setze Download fort",
                    self._exchange.name,
                    symbol,
                    timeframe.value,
                    last,
                )

        if effective_start >= end:
            logger.info("%s %s %s: Daten bereits aktuell", self._exchange.name, symbol, timeframe.value)
            return self._storage.load(
                self._exchange.name, symbol, timeframe,
                start=pd.Timestamp(start), end=pd.Timestamp(end),
            )

        since_ms = int(effective_start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        total = 0

        while since_ms < end_ms:
            candles = await retry_async(
                lambda since=since_ms: self._exchange.fetch_ohlcv(
                    symbol, timeframe, since=since, limit=self._batch_size
                ),
                description=f"fetch_ohlcv {symbol} {timeframe.value}",
            )
            if not candles:
                break

            batch_df = candles_to_dataframe(candles)
            batch_df = batch_df[batch_df.index <= pd.Timestamp(end)]
            if batch_df.empty:
                break
            self._storage.append(batch_df, self._exchange.name, symbol, timeframe)
            total += len(batch_df)

            last_ts_ms = int(batch_df.index[-1].timestamp() * 1000)
            next_since = last_ts_ms + timeframe.milliseconds
            if next_since <= since_ms:
                # Schutz gegen Endlosschleifen bei fehlerhaften API-Antworten.
                break
            since_ms = next_since

            if len(candles) < 2:
                break

        logger.info(
            "%s %s %s: %d neue Kerzen heruntergeladen",
            self._exchange.name,
            symbol,
            timeframe.value,
            total,
        )
        return self._storage.load(
            self._exchange.name, symbol, timeframe,
            start=pd.Timestamp(start), end=pd.Timestamp(end),
        )

    async def download_many(
        self,
        symbols: list[str],
        timeframes: list[Timeframe],
        start: datetime,
        end: datetime | None = None,
    ) -> dict[tuple[str, Timeframe], pd.DataFrame]:
        """Lädt mehrere Symbole/Timeframes sequenziell (rate-limit-schonend).

        Returns:
            Mapping ``(symbol, timeframe) -> DataFrame``.
        """
        results: dict[tuple[str, Timeframe], pd.DataFrame] = {}
        for symbol in symbols:
            for timeframe in timeframes:
                try:
                    results[(symbol, timeframe)] = await self.download(
                        symbol, timeframe, start, end
                    )
                except Exception:
                    logger.exception(
                        "Download fehlgeschlagen: %s %s – fahre mit nächstem Datensatz fort",
                        symbol,
                        timeframe.value,
                    )
        return results
