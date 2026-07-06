"""Lokale Speicherung historischer Kerzendaten im Parquet-Format.

Ablagestruktur::

    <storage_dir>/<exchange>/<symbol_slug>/<timeframe>.parquet

Alle DataFrames verwenden einen UTC-DatetimeIndex namens ``timestamp``
und die Spalten ``open, high, low, close, volume``.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from tradingbot.core.enums import Timeframe
from tradingbot.core.exceptions import DataError
from tradingbot.core.logging import get_logger
from tradingbot.core.models import Candle

logger = get_logger(__name__)

OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]


def symbol_slug(symbol: str) -> str:
    """Wandelt ein Symbol in einen dateisystem-sicheren Namen um."""
    return symbol.replace("/", "_").replace(":", "-")


def candles_to_dataframe(candles: list[Candle]) -> pd.DataFrame:
    """Konvertiert Candle-Objekte in einen OHLCV-DataFrame.

    Args:
        candles: Liste von Kerzen (beliebige Reihenfolge, Duplikate erlaubt).

    Returns:
        Zeitlich sortierter, deduplizierter DataFrame mit UTC-DatetimeIndex.
    """
    if not candles:
        return pd.DataFrame(columns=OHLCV_COLUMNS, index=pd.DatetimeIndex([], name="timestamp", tz="UTC"))
    df = pd.DataFrame(
        {
            "timestamp": [c.timestamp for c in candles],
            "open": [c.open for c in candles],
            "high": [c.high for c in candles],
            "low": [c.low for c in candles],
            "close": [c.close for c in candles],
            "volume": [c.volume for c in candles],
        }
    )
    df = df.set_index("timestamp").sort_index()
    df.index = pd.DatetimeIndex(df.index, tz="UTC") if df.index.tz is None else df.index.tz_convert("UTC")
    return df[~df.index.duplicated(keep="last")]


def dataframe_to_candles(df: pd.DataFrame, symbol: str, timeframe: Timeframe) -> list[Candle]:
    """Konvertiert einen OHLCV-DataFrame zurück in Candle-Objekte."""
    candles: list[Candle] = []
    for timestamp, row in df.iterrows():
        candles.append(
            Candle(
                symbol=symbol,
                timeframe=timeframe,
                timestamp=timestamp.to_pydatetime(),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
            )
        )
    return candles


class CandleStorage:
    """Parquet-basierter Speicher für historische Kerzendaten.

    Args:
        storage_dir: Wurzelverzeichnis der Ablage.
    """

    def __init__(self, storage_dir: str | Path) -> None:
        self._root = Path(storage_dir)
        self._root.mkdir(parents=True, exist_ok=True)

    def path_for(self, exchange: str, symbol: str, timeframe: Timeframe) -> Path:
        """Dateipfad für eine Exchange/Symbol/Timeframe-Kombination."""
        return self._root / exchange / symbol_slug(symbol) / f"{timeframe.value}.parquet"

    def exists(self, exchange: str, symbol: str, timeframe: Timeframe) -> bool:
        """True, wenn lokale Daten vorhanden sind."""
        return self.path_for(exchange, symbol, timeframe).exists()

    def save(
        self, df: pd.DataFrame, exchange: str, symbol: str, timeframe: Timeframe
    ) -> Path:
        """Speichert einen OHLCV-DataFrame (überschreibt vorhandene Datei).

        Args:
            df: Zu speichernder DataFrame.
            exchange: Börsenname.
            symbol: Handelspaar.
            timeframe: Kerzen-Timeframe.

        Returns:
            Pfad der geschriebenen Datei.

        Raises:
            DataError: Bei fehlenden OHLCV-Spalten.
        """
        self._validate(df)
        path = self.path_for(exchange, symbol, timeframe)
        path.parent.mkdir(parents=True, exist_ok=True)
        df.sort_index().to_parquet(path)
        logger.debug("%d Kerzen gespeichert: %s", len(df), path)
        return path

    def load(
        self,
        exchange: str,
        symbol: str,
        timeframe: Timeframe,
        start: pd.Timestamp | None = None,
        end: pd.Timestamp | None = None,
    ) -> pd.DataFrame:
        """Lädt lokale Kerzendaten, optional auf einen Zeitraum begrenzt.

        Args:
            exchange: Börsenname.
            symbol: Handelspaar.
            timeframe: Kerzen-Timeframe.
            start: Inklusiver Startzeitpunkt (None = Anfang).
            end: Inklusiver Endzeitpunkt (None = Ende).

        Returns:
            OHLCV-DataFrame (leer, wenn keine Daten vorhanden).
        """
        path = self.path_for(exchange, symbol, timeframe)
        if not path.exists():
            return pd.DataFrame(
                columns=OHLCV_COLUMNS, index=pd.DatetimeIndex([], name="timestamp", tz="UTC")
            )
        df = pd.read_parquet(path)
        if start is not None:
            df = df[df.index >= start]
        if end is not None:
            df = df[df.index <= end]
        return df

    def append(
        self, df: pd.DataFrame, exchange: str, symbol: str, timeframe: Timeframe
    ) -> pd.DataFrame:
        """Fügt neue Kerzen zu vorhandenen Daten hinzu (dedupliziert).

        Bei Zeitstempel-Kollisionen gewinnen die neuen Werte.

        Returns:
            Der zusammengeführte Gesamtbestand.
        """
        self._validate(df)
        existing = self.load(exchange, symbol, timeframe)
        combined = pd.concat([existing, df])
        combined = combined[~combined.index.duplicated(keep="last")].sort_index()
        self.save(combined, exchange, symbol, timeframe)
        return combined

    def last_timestamp(
        self, exchange: str, symbol: str, timeframe: Timeframe
    ) -> pd.Timestamp | None:
        """Zeitstempel der jüngsten lokalen Kerze oder None."""
        df = self.load(exchange, symbol, timeframe)
        if df.empty:
            return None
        return df.index[-1]

    def list_datasets(self) -> list[dict[str, str]]:
        """Listet alle lokal vorhandenen Datensätze auf.

        Returns:
            Liste von Dicts mit ``exchange``, ``symbol``, ``timeframe``, ``path``.
        """
        datasets: list[dict[str, str]] = []
        for parquet_file in sorted(self._root.glob("*/*/*.parquet")):
            datasets.append(
                {
                    "exchange": parquet_file.parent.parent.name,
                    "symbol": parquet_file.parent.name.replace("_", "/"),
                    "timeframe": parquet_file.stem,
                    "path": str(parquet_file),
                }
            )
        return datasets

    @staticmethod
    def _validate(df: pd.DataFrame) -> None:
        """Prüft das OHLCV-Schema eines DataFrames."""
        missing = [col for col in OHLCV_COLUMNS if col not in df.columns]
        if missing:
            raise DataError(f"DataFrame fehlt OHLCV-Spalten: {missing}")
        if not isinstance(df.index, pd.DatetimeIndex):
            raise DataError("DataFrame benötigt einen DatetimeIndex")
