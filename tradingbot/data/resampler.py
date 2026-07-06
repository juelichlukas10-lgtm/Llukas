"""Multi-Timeframe-Resampling von OHLCV-Daten.

Aggregiert Kerzen eines feineren Timeframes verlustfrei in einen
gröberen (z. B. 1m -> 15m). Downsampling in feinere Timeframes ist
nicht möglich und wird abgelehnt.
"""

from __future__ import annotations

import pandas as pd

from tradingbot.core.enums import Timeframe
from tradingbot.core.exceptions import DataError

_AGGREGATION = {
    "open": "first",
    "high": "max",
    "low": "min",
    "close": "last",
    "volume": "sum",
}


def resample_candles(
    df: pd.DataFrame, source: Timeframe, target: Timeframe, drop_incomplete: bool = True
) -> pd.DataFrame:
    """Resampelt OHLCV-Daten von ``source`` nach ``target``.

    Args:
        df: OHLCV-DataFrame mit DatetimeIndex im ``source``-Timeframe.
        source: Timeframe der Eingabedaten.
        target: Gewünschter (gröberer) Ziel-Timeframe.
        drop_incomplete: Entfernt die letzte Zielkerze, wenn sie nicht
            vollständig durch Quellkerzen abgedeckt ist.

    Returns:
        Resampelter OHLCV-DataFrame.

    Raises:
        DataError: Wenn ``target`` feiner oder gleich ``source`` ist oder
            kein DatetimeIndex vorliegt.
    """
    if target.seconds < source.seconds:
        raise DataError(
            f"Resampling von {source.value} nach {target.value} nicht möglich "
            f"(Ziel ist feiner als Quelle)"
        )
    if not isinstance(df.index, pd.DatetimeIndex):
        raise DataError("Resampling benötigt einen DatetimeIndex")
    if target is source or df.empty:
        return df.copy()

    resampled = (
        df.resample(target.pandas_freq, label="left", closed="left").agg(_AGGREGATION).dropna()
    )

    if drop_incomplete and not resampled.empty:
        candles_per_bucket = target.seconds // source.seconds
        last_bucket_start = resampled.index[-1]
        last_bucket_end = last_bucket_start + pd.Timedelta(seconds=target.seconds)
        source_in_last = df[(df.index >= last_bucket_start) & (df.index < last_bucket_end)]
        if len(source_in_last) < candles_per_bucket:
            resampled = resampled.iloc[:-1]

    return resampled
