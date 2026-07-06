"""Technische Indikatoren auf Basis von pandas/numpy.

Alle Funktionen sind vektorisiert, frei von Seiteneffekten und arbeiten
auf einem OHLCV-DataFrame mit den Spalten ``open, high, low, close,
volume`` und einem DatetimeIndex. Rückgaben sind Series bzw. DataFrames
mit demselben Index; nicht berechenbare Anfangswerte sind ``NaN``.

Konventionen:
    * Wilder-Glättung (RSI, ATR, ADX) über ``ewm(alpha=1/period)``.
    * Keine implizite Vorwärtsfüllung – der Aufrufer entscheidet über
      den Umgang mit ``NaN``-Werten.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Gleitende Durchschnitte
# ---------------------------------------------------------------------------


def sma(series: pd.Series, period: int) -> pd.Series:
    """Simple Moving Average.

    Args:
        series: Eingabeserie (üblicherweise Schlusskurse).
        period: Fensterlänge.
    """
    _validate_period(period)
    return series.rolling(window=period, min_periods=period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential Moving Average (Startwerte erst ab ``period`` gültig).

    Args:
        series: Eingabeserie.
        period: Glättungsperiode.
    """
    _validate_period(period)
    result = series.ewm(span=period, adjust=False, min_periods=period).mean()
    return result


def wilder_smooth(series: pd.Series, period: int) -> pd.Series:
    """Wilder-Glättung (RMA), wie von RSI/ATR/ADX verwendet."""
    _validate_period(period)
    return series.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


# ---------------------------------------------------------------------------
# Momentum-Indikatoren
# ---------------------------------------------------------------------------


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index nach Wilder in ``[0, 100]``.

    Args:
        series: Schlusskurse.
        period: RSI-Periode (Standard 14).
    """
    _validate_period(period)
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = wilder_smooth(gain, period)
    avg_loss = wilder_smooth(loss, period)
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    result = 100.0 - 100.0 / (1.0 + rs)
    # Wenn avg_loss == 0 und avg_gain > 0 -> RSI = 100; beide 0 -> 50.
    result = result.where(avg_loss != 0, np.where(avg_gain > 0, 100.0, 50.0))
    result[avg_gain.isna() | avg_loss.isna()] = np.nan
    return result


def macd(
    series: pd.Series,
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> pd.DataFrame:
    """MACD mit Signallinie und Histogramm.

    Returns:
        DataFrame mit Spalten ``macd``, ``signal``, ``histogram``.
    """
    macd_line = ema(series, fast_period) - ema(series, slow_period)
    signal_line = macd_line.ewm(span=signal_period, adjust=False, min_periods=signal_period).mean()
    histogram = macd_line - signal_line
    return pd.DataFrame({"macd": macd_line, "signal": signal_line, "histogram": histogram})


def stochastic(
    df: pd.DataFrame, k_period: int = 14, d_period: int = 3, smooth_k: int = 3
) -> pd.DataFrame:
    """Stochastic Oscillator (%K, %D) in ``[0, 100]``.

    Returns:
        DataFrame mit Spalten ``k`` und ``d``.
    """
    _validate_period(k_period)
    lowest = df["low"].rolling(k_period, min_periods=k_period).min()
    highest = df["high"].rolling(k_period, min_periods=k_period).max()
    span = (highest - lowest).replace(0.0, np.nan)
    raw_k = 100.0 * (df["close"] - lowest) / span
    k = raw_k.rolling(smooth_k, min_periods=smooth_k).mean()
    d = k.rolling(d_period, min_periods=d_period).mean()
    return pd.DataFrame({"k": k, "d": d})


def cci(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """Commodity Channel Index.

    Args:
        df: OHLCV-DataFrame.
        period: Fensterlänge (Standard 20).
    """
    _validate_period(period)
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    sma_tp = typical.rolling(period, min_periods=period).mean()
    mean_dev = typical.rolling(period, min_periods=period).apply(
        lambda window: np.mean(np.abs(window - window.mean())), raw=True
    )
    return (typical - sma_tp) / (0.015 * mean_dev.replace(0.0, np.nan))


def momentum(series: pd.Series, period: int = 10) -> pd.Series:
    """Prozentuale Kursänderung über ``period`` Kerzen (Rate of Change)."""
    _validate_period(period)
    return series.pct_change(periods=period)


def mfi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Money Flow Index in ``[0, 100]`` (volumengewichteter RSI)."""
    _validate_period(period)
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    raw_flow = typical * df["volume"]
    direction = typical.diff()
    positive = raw_flow.where(direction > 0, 0.0)
    negative = raw_flow.where(direction < 0, 0.0)
    pos_sum = positive.rolling(period, min_periods=period).sum()
    neg_sum = negative.rolling(period, min_periods=period).sum()
    ratio = pos_sum / neg_sum.replace(0.0, np.nan)
    result = 100.0 - 100.0 / (1.0 + ratio)
    result = result.where(neg_sum != 0, np.where(pos_sum > 0, 100.0, 50.0))
    result[pos_sum.isna() | neg_sum.isna()] = np.nan
    return result


# ---------------------------------------------------------------------------
# Volatilitäts-Indikatoren
# ---------------------------------------------------------------------------


def true_range(df: pd.DataFrame) -> pd.Series:
    """True Range je Kerze."""
    prev_close = df["close"].shift(1)
    ranges = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    )
    return ranges.max(axis=1)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range nach Wilder."""
    return wilder_smooth(true_range(df), period)


def bollinger_bands(series: pd.Series, period: int = 20, std_dev: float = 2.0) -> pd.DataFrame:
    """Bollinger-Bänder.

    Returns:
        DataFrame mit ``upper``, ``middle``, ``lower``, ``bandwidth``, ``percent_b``.
    """
    _validate_period(period)
    middle = sma(series, period)
    std = series.rolling(period, min_periods=period).std(ddof=0)
    upper = middle + std_dev * std
    lower = middle - std_dev * std
    bandwidth = (upper - lower) / middle.replace(0.0, np.nan)
    percent_b = (series - lower) / (upper - lower).replace(0.0, np.nan)
    return pd.DataFrame(
        {"upper": upper, "middle": middle, "lower": lower, "bandwidth": bandwidth, "percent_b": percent_b}
    )


def keltner_channel(
    df: pd.DataFrame, period: int = 20, atr_period: int = 10, multiplier: float = 2.0
) -> pd.DataFrame:
    """Keltner Channel (EMA ± Multiplikator × ATR).

    Returns:
        DataFrame mit ``upper``, ``middle``, ``lower``.
    """
    middle = ema(df["close"], period)
    atr_val = atr(df, atr_period)
    return pd.DataFrame(
        {"upper": middle + multiplier * atr_val, "middle": middle, "lower": middle - multiplier * atr_val}
    )


def donchian_channel(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
    """Donchian Channel (höchstes Hoch / tiefstes Tief).

    Returns:
        DataFrame mit ``upper``, ``middle``, ``lower``.
    """
    _validate_period(period)
    upper = df["high"].rolling(period, min_periods=period).max()
    lower = df["low"].rolling(period, min_periods=period).min()
    middle = (upper + lower) / 2.0
    return pd.DataFrame({"upper": upper, "middle": middle, "lower": lower})


# ---------------------------------------------------------------------------
# Trend-Indikatoren
# ---------------------------------------------------------------------------


def adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """Average Directional Index mit +DI und -DI.

    Returns:
        DataFrame mit ``adx``, ``plus_di``, ``minus_di``.
    """
    _validate_period(period)
    up_move = df["high"].diff()
    down_move = -df["low"].diff()
    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index
    )
    atr_val = wilder_smooth(true_range(df), period)
    plus_di = 100.0 * wilder_smooth(plus_dm, period) / atr_val.replace(0.0, np.nan)
    minus_di = 100.0 * wilder_smooth(minus_dm, period) / atr_val.replace(0.0, np.nan)
    di_sum = (plus_di + minus_di).replace(0.0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / di_sum
    adx_val = wilder_smooth(dx, period)
    return pd.DataFrame({"adx": adx_val, "plus_di": plus_di, "minus_di": minus_di})


def supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0) -> pd.DataFrame:
    """Supertrend-Indikator.

    Returns:
        DataFrame mit ``supertrend`` (Stop-Linie) und ``direction``
        (+1 = Aufwärtstrend, -1 = Abwärtstrend).
    """
    _validate_period(period)
    atr_val = atr(df, period)
    hl2 = (df["high"] + df["low"]) / 2.0
    upper_band = (hl2 + multiplier * atr_val).to_numpy()
    lower_band = (hl2 - multiplier * atr_val).to_numpy()
    close = df["close"].to_numpy()

    n = len(df)
    st = np.full(n, np.nan)
    direction = np.full(n, 0.0)
    final_upper = np.full(n, np.nan)
    final_lower = np.full(n, np.nan)

    start = int(np.argmax(~np.isnan(atr_val.to_numpy()))) if n > 0 else 0
    if n == 0 or np.isnan(atr_val.to_numpy()).all():
        return pd.DataFrame({"supertrend": pd.Series(st, index=df.index),
                             "direction": pd.Series(direction, index=df.index)})

    final_upper[start] = upper_band[start]
    final_lower[start] = lower_band[start]
    direction[start] = 1.0
    st[start] = lower_band[start]

    for i in range(start + 1, n):
        final_upper[i] = (
            upper_band[i]
            if upper_band[i] < final_upper[i - 1] or close[i - 1] > final_upper[i - 1]
            else final_upper[i - 1]
        )
        final_lower[i] = (
            lower_band[i]
            if lower_band[i] > final_lower[i - 1] or close[i - 1] < final_lower[i - 1]
            else final_lower[i - 1]
        )
        if direction[i - 1] > 0:
            direction[i] = -1.0 if close[i] < final_lower[i] else 1.0
        else:
            direction[i] = 1.0 if close[i] > final_upper[i] else -1.0
        st[i] = final_lower[i] if direction[i] > 0 else final_upper[i]

    return pd.DataFrame(
        {"supertrend": pd.Series(st, index=df.index), "direction": pd.Series(direction, index=df.index)}
    )


def parabolic_sar(
    df: pd.DataFrame, af_start: float = 0.02, af_step: float = 0.02, af_max: float = 0.2
) -> pd.Series:
    """Parabolic SAR nach Wilder.

    Args:
        df: OHLCV-DataFrame.
        af_start: Start-Beschleunigungsfaktor.
        af_step: Inkrement bei neuem Extrempunkt.
        af_max: Maximaler Beschleunigungsfaktor.
    """
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    n = len(df)
    sar = np.full(n, np.nan)
    if n < 2:
        return pd.Series(sar, index=df.index)

    uptrend = high[1] >= high[0]
    af = af_start
    ep = high[0] if uptrend else low[0]
    sar[0] = low[0] if uptrend else high[0]

    for i in range(1, n):
        prev_sar = sar[i - 1]
        current = prev_sar + af * (ep - prev_sar)
        if uptrend:
            current = min(current, low[i - 1], low[i - 2] if i >= 2 else low[i - 1])
            if low[i] < current:
                uptrend = False
                current = ep
                ep = low[i]
                af = af_start
            elif high[i] > ep:
                ep = high[i]
                af = min(af + af_step, af_max)
        else:
            current = max(current, high[i - 1], high[i - 2] if i >= 2 else high[i - 1])
            if high[i] > current:
                uptrend = True
                current = ep
                ep = high[i]
                af = af_start
            elif low[i] < ep:
                ep = low[i]
                af = min(af + af_step, af_max)
        sar[i] = current

    return pd.Series(sar, index=df.index)


def ichimoku(
    df: pd.DataFrame,
    tenkan_period: int = 9,
    kijun_period: int = 26,
    senkou_b_period: int = 52,
    displacement: int = 26,
) -> pd.DataFrame:
    """Ichimoku Kinko Hyo.

    Returns:
        DataFrame mit ``tenkan``, ``kijun``, ``senkou_a``, ``senkou_b``,
        ``chikou``. Senkou-Spannen sind um ``displacement`` nach vorn,
        Chikou um ``displacement`` nach hinten versetzt.
    """

    def _midline(period: int) -> pd.Series:
        hi = df["high"].rolling(period, min_periods=period).max()
        lo = df["low"].rolling(period, min_periods=period).min()
        return (hi + lo) / 2.0

    tenkan = _midline(tenkan_period)
    kijun = _midline(kijun_period)
    senkou_a = ((tenkan + kijun) / 2.0).shift(displacement)
    senkou_b = _midline(senkou_b_period).shift(displacement)
    chikou = df["close"].shift(-displacement)
    return pd.DataFrame(
        {"tenkan": tenkan, "kijun": kijun, "senkou_a": senkou_a, "senkou_b": senkou_b, "chikou": chikou}
    )


# ---------------------------------------------------------------------------
# Volumen-Indikatoren
# ---------------------------------------------------------------------------


def vwap(df: pd.DataFrame, reset_daily: bool = True) -> pd.Series:
    """Volume Weighted Average Price.

    Args:
        df: OHLCV-DataFrame mit DatetimeIndex.
        reset_daily: True setzt die Kumulation an jedem Kalendertag zurück
            (Intraday-VWAP); False kumuliert über den gesamten DataFrame.
    """
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    tp_vol = typical * df["volume"]
    if reset_daily and isinstance(df.index, pd.DatetimeIndex):
        grouper = df.index.normalize()
        cum_tp_vol = tp_vol.groupby(grouper).cumsum()
        cum_vol = df["volume"].groupby(grouper).cumsum()
    else:
        cum_tp_vol = tp_vol.cumsum()
        cum_vol = df["volume"].cumsum()
    return cum_tp_vol / cum_vol.replace(0.0, np.nan)


def obv(df: pd.DataFrame) -> pd.Series:
    """On-Balance Volume."""
    direction = np.sign(df["close"].diff()).fillna(0.0)
    return (direction * df["volume"]).cumsum()


def volume_profile(df: pd.DataFrame, bins: int = 24) -> pd.DataFrame:
    """Volumenprofil über Preisniveaus (optionaler Indikator).

    Aggregiert das Volumen je Preis-Bin (auf Basis des typischen Preises).

    Args:
        df: OHLCV-DataFrame.
        bins: Anzahl der Preis-Bins.

    Returns:
        DataFrame mit ``price`` (Bin-Mitte) und ``volume``, absteigend
        nach Volumen sortiert (erste Zeile = Point of Control).
    """
    if df.empty:
        return pd.DataFrame({"price": [], "volume": []})
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    lo, hi = float(typical.min()), float(typical.max())
    if hi <= lo:
        return pd.DataFrame({"price": [lo], "volume": [float(df["volume"].sum())]})
    edges = np.linspace(lo, hi, bins + 1)
    labels = (edges[:-1] + edges[1:]) / 2.0
    binned = pd.cut(typical, bins=edges, labels=labels, include_lowest=True)
    profile = df["volume"].groupby(binned, observed=True).sum()
    result = pd.DataFrame({"price": profile.index.astype(float), "volume": profile.to_numpy()})
    return result.sort_values("volume", ascending=False).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Pivot Points
# ---------------------------------------------------------------------------


def pivot_points(df: pd.DataFrame) -> pd.DataFrame:
    """Klassische Pivot-Punkte auf Basis der jeweiligen Vorkerze.

    Für Tages-Pivots den DataFrame zuvor auf ``1d`` resampeln.

    Returns:
        DataFrame mit ``pivot``, ``r1``, ``r2``, ``r3``, ``s1``, ``s2``, ``s3``.
    """
    high = df["high"].shift(1)
    low = df["low"].shift(1)
    close = df["close"].shift(1)
    pivot = (high + low + close) / 3.0
    r1 = 2.0 * pivot - low
    s1 = 2.0 * pivot - high
    r2 = pivot + (high - low)
    s2 = pivot - (high - low)
    r3 = high + 2.0 * (pivot - low)
    s3 = low - 2.0 * (high - pivot)
    return pd.DataFrame({"pivot": pivot, "r1": r1, "r2": r2, "r3": r3, "s1": s1, "s2": s2, "s3": s3})


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------


def crossover(fast: pd.Series, slow: pd.Series) -> pd.Series:
    """True an Stellen, an denen ``fast`` die ``slow``-Serie von unten kreuzt."""
    prev_fast, prev_slow = fast.shift(1), slow.shift(1)
    return (prev_fast <= prev_slow) & (fast > slow)


def crossunder(fast: pd.Series, slow: pd.Series) -> pd.Series:
    """True an Stellen, an denen ``fast`` die ``slow``-Serie von oben kreuzt."""
    prev_fast, prev_slow = fast.shift(1), slow.shift(1)
    return (prev_fast >= prev_slow) & (fast < slow)


def _validate_period(period: int) -> None:
    """Stellt sicher, dass eine Periode ein Integer >= 1 ist."""
    if not isinstance(period, int) or period < 1:
        raise ValueError(f"Periode muss ein positiver Integer sein, erhalten: {period!r}")
