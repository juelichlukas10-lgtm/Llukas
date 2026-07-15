"""Buy-the-Dip-Mustererkennung.

Erkennt anhand objektiver, konfigurierbarer Regeln Situationen, in denen
eine Aktie in einem intakten Aufwärtstrend geordnet korrigiert und sich
einer relevanten Unterstützung nähert:

    1. **Trendfilter** – Kurs über den langen EMAs, EMA-Stapelung,
       positiver EMA-Slope, höhere Tiefs, deutlicher Anstieg über den
       Beobachtungszeitraum.
    2. **Rücksetzer** – definierter Abstand vom jüngsten Hoch innerhalb
       eines Zeitfensters, ohne Panik-Charakter (kein Tagesverlust über
       ``panic_atr_mult`` × ATR, kein extremer Volumen-Spike).
    3. **Unterstützung** – Nähe zu EMA20/50/100/200, einem früheren
       Ausbruchsniveau oder einem Fibonacci-Retracement (38.2/50/61.8).
    4. **Stabilisierung** – bullische Kerzenformationen (Hammer,
       Bullish Engulfing, starker Schluss), RSI-Drehung, anziehendes
       Kaufvolumen.

Der Detektor liefert für jedes Symbol höchstens ein
:class:`~tradingbot.scanner.models.DipSignal` – oder ``None``, wenn die
Kriterien nicht erfüllt sind.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from tradingbot.analytics.indicators import atr as atr_indicator
from tradingbot.analytics.indicators import ema, rsi
from tradingbot.core.logging import get_logger
from tradingbot.scanner.models import DipSignal, SetupStatus, SupportType
from tradingbot.scanner.scoring import DipScorer, ScoreFactors

logger = get_logger(__name__)


@dataclass(slots=True)
class DetectorConfig:
    """Alle Parameter der Mustererkennung (vollständig konfigurierbar).

    Attributes:
        min_history: Mindestanzahl Tageskerzen für eine Analyse.
        trend_lookback: Fenster der Trendbeurteilung in Kerzen.
        min_trend_gain: Mindestanstieg vom Fensterbeginn bis zum Hoch.
        high_lookback: Fenster für das Bezugshoch des Rücksetzers.
        min_dip: Mindest-Rücksetzer vom Hoch (Bruchteil).
        max_dip: Maximaler Rücksetzer (darüber gilt der Trend als gebrochen).
        min_dip_bars: Mindestalter des Hochs in Kerzen.
        max_dip_bars: Maximalalter des Hochs in Kerzen.
        panic_atr_mult: Max. Tagesverlust im Rücksetzer als ATR-Vielfaches.
        volume_spike_limit: Max. Abwärtstages-Volumen relativ zum Schnitt.
        support_max_distance: Max. Abstand über der Unterstützung (Bruchteil).
        support_undercut_tolerance: Erlaubtes Unterschreiten der
            Unterstützung (Bruchteil, positiv angegeben).
        invalidation_pct: Bruch der Unterstützung, der das Setup ungültig macht.
        stop_atr_mult: ATR-Puffer unter dem Rücksetzer-Tief für den Stop.
        min_trend_score: Mindest-Trendqualität in [0, 1].
        rs_lookback: Fenster der relativen Stärke vs. Benchmark in Kerzen.
    """

    min_history: int = 120
    trend_lookback: int = 120
    min_trend_gain: float = 0.10
    high_lookback: int = 60
    min_dip: float = 0.03
    max_dip: float = 0.20
    min_dip_bars: int = 2
    max_dip_bars: int = 30
    panic_atr_mult: float = 2.5
    volume_spike_limit: float = 2.25
    support_max_distance: float = 0.04
    support_undercut_tolerance: float = 0.015
    invalidation_pct: float = 0.03
    stop_atr_mult: float = 0.5
    min_trend_score: float = 0.6
    rs_lookback: int = 63


@dataclass(slots=True)
class _Indicators:
    """Vorberechnete Indikatoren eines Symbols."""

    ema20: pd.Series
    ema50: pd.Series
    ema100: pd.Series
    ema200: pd.Series | None
    rsi14: pd.Series
    atr14: pd.Series
    avg_volume: pd.Series


class DipDetector:
    """Erkennt Buy-the-Dip-Setups in Tages-OHLCV-Daten.

    Args:
        config: Detektor-Parameter.
        scorer: Bewertungsmodul (Score 0–100).
    """

    def __init__(self, config: DetectorConfig | None = None, scorer: DipScorer | None = None) -> None:
        self.config = config or DetectorConfig()
        self.scorer = scorer or DipScorer()

    # ------------------------------------------------------------------
    # Öffentliche API
    # ------------------------------------------------------------------

    def detect(
        self,
        symbol: str,
        name: str,
        df: pd.DataFrame,
        benchmark: pd.DataFrame | None = None,
    ) -> DipSignal | None:
        """Analysiert ein Symbol und liefert ggf. ein Setup.

        Args:
            symbol: Tickersymbol.
            name: Anzeigename.
            df: Tages-OHLCV (Spalten ``open, high, low, close, volume``).
            benchmark: OHLCV der Marktbenchmark für relative Stärke.

        Returns:
            :class:`DipSignal` oder None (kein valides Setup).
        """
        cfg = self.config
        if len(df) < cfg.min_history:
            return None

        ind = self._compute_indicators(df)
        close = float(df["close"].iloc[-1])
        if close <= 0 or pd.isna(ind.atr14.iloc[-1]) or float(ind.atr14.iloc[-1]) <= 0:
            return None

        # --- 1. Trendfilter -------------------------------------------------
        trend_strength = self._trend_strength(df, ind)
        if trend_strength < cfg.min_trend_score:
            return None

        # --- 2. Rücksetzer ---------------------------------------------------
        pullback = self._analyze_pullback(df, ind)
        if pullback is None:
            return None
        high_pos, recent_high, drawdown, orderly, down_volume_ratio = pullback

        # --- 3. Unterstützung ------------------------------------------------
        support = self._nearest_support(df, ind, high_pos, recent_high, close)
        if support is None:
            return None
        support_type, support_level = support
        support_distance = (close - support_level) / close

        # Ungültig: Unterstützung klar gebrochen.
        if close < support_level * (1.0 - cfg.invalidation_pct):
            return None

        # --- 4. Stabilisierung & Status --------------------------------------
        stabilization = self._stabilization_score(df, ind, high_pos)
        status = self._determine_status(df, ind, stabilization)

        # --- 5. Level & Kennzahlen -------------------------------------------
        atr_value = float(ind.atr14.iloc[-1])
        pullback_low = float(df["low"].iloc[high_pos:].min())
        entry_price = max(float(df["high"].iloc[-3:].max()), close) * 1.001
        stop_loss = min(support_level * 0.99, pullback_low) - cfg.stop_atr_mult * atr_value
        target_1 = recent_high
        target_2 = recent_high + (recent_high - pullback_low)
        risk = entry_price - stop_loss
        risk_reward = (target_1 - entry_price) / risk if risk > 0 else 0.0
        if risk_reward <= 0:
            return None

        prev_close = float(df["close"].iloc[-2])
        change_pct = close / prev_close - 1.0 if prev_close > 0 else 0.0
        volume = float(df["volume"].iloc[-1])
        avg_vol = float(ind.avg_volume.iloc[-1]) if not pd.isna(ind.avg_volume.iloc[-1]) else 0.0
        volume_ratio = volume / avg_vol if avg_vol > 0 else 1.0
        rsi_value = float(ind.rsi14.iloc[-1]) if not pd.isna(ind.rsi14.iloc[-1]) else 50.0
        relative_strength = self._relative_strength(df, benchmark)

        factors = ScoreFactors(
            trend_strength=trend_strength,
            drawdown_pct=drawdown,
            support_distance_pct=support_distance,
            orderly=orderly,
            down_volume_ratio=down_volume_ratio,
            stabilization=stabilization,
            rsi=rsi_value,
            relative_strength=relative_strength,
            risk_reward=risk_reward,
            status=status,
        )
        score, breakdown = self.scorer.score(factors)

        return DipSignal(
            symbol=symbol,
            name=name,
            status=status,
            score=score,
            price=close,
            change_pct=change_pct,
            recent_high=recent_high,
            drawdown_pct=drawdown,
            support_type=support_type,
            support_level=support_level,
            support_distance_pct=support_distance,
            trend_strength=trend_strength,
            rsi=rsi_value,
            volume=volume,
            volume_ratio=volume_ratio,
            relative_strength=relative_strength,
            atr=atr_value,
            entry_price=entry_price,
            stop_loss=stop_loss,
            target_1=target_1,
            target_2=target_2,
            risk_reward=risk_reward,
            score_breakdown=breakdown,
        )

    # ------------------------------------------------------------------
    # Indikatoren
    # ------------------------------------------------------------------

    def _compute_indicators(self, df: pd.DataFrame) -> _Indicators:
        close = df["close"]
        return _Indicators(
            ema20=ema(close, 20),
            ema50=ema(close, 50),
            ema100=ema(close, 100),
            ema200=ema(close, 200) if len(df) >= 200 else None,
            rsi14=rsi(close, 14),
            atr14=atr_indicator(df, 14),
            avg_volume=df["volume"].rolling(20, min_periods=20).mean(),
        )

    # ------------------------------------------------------------------
    # 1. Trend
    # ------------------------------------------------------------------

    def _trend_strength(self, df: pd.DataFrame, ind: _Indicators) -> float:
        """Trendqualität in [0, 1] aus mehreren gleichgewichteten Kriterien."""
        cfg = self.config
        close = float(df["close"].iloc[-1])
        checks: list[bool] = []

        # Kurs über den langen Durchschnitten.
        ema100_now = float(ind.ema100.iloc[-1])
        checks.append(not pd.isna(ema100_now) and close > ema100_now)
        if ind.ema200 is not None and not pd.isna(ind.ema200.iloc[-1]):
            checks.append(close > float(ind.ema200.iloc[-1]))
            checks.append(float(ind.ema50.iloc[-1]) > float(ind.ema200.iloc[-1]))
        else:
            checks.append(float(ind.ema50.iloc[-1]) > ema100_now)

        # Positiver EMA50-Slope.
        if len(ind.ema50.dropna()) > 20:
            checks.append(float(ind.ema50.iloc[-1]) > float(ind.ema50.iloc[-21]))

        # Deutlicher Anstieg über das Trendfenster.
        window = df.tail(cfg.trend_lookback)
        start_close = float(window["close"].iloc[0])
        window_high = float(window["high"].max())
        checks.append(start_close > 0 and window_high / start_close - 1.0 >= cfg.min_trend_gain)

        # Höhere Tiefs: jüngste Fensterhälfte über der älteren.
        half = len(window) // 2
        if half >= 10:
            older_low = float(window["low"].iloc[:half].min())
            recent_low = float(window["low"].iloc[half:].min())
            checks.append(recent_low > older_low)

        return sum(checks) / len(checks) if checks else 0.0

    # ------------------------------------------------------------------
    # 2. Rücksetzer
    # ------------------------------------------------------------------

    def _analyze_pullback(
        self, df: pd.DataFrame, ind: _Indicators
    ) -> tuple[int, float, float, bool, float] | None:
        """Prüft Existenz und Charakter des Rücksetzers.

        Returns:
            (Position des Hochs, Hoch, Drawdown, geordnet?, Abwärts-Volumen-Ratio)
            oder None, wenn kein valider Rücksetzer vorliegt.
        """
        cfg = self.config
        window = df.tail(cfg.high_lookback)
        high_pos_in_window = int(np.argmax(window["high"].to_numpy()))
        high_pos = len(df) - len(window) + high_pos_in_window
        recent_high = float(window["high"].iloc[high_pos_in_window])
        close = float(df["close"].iloc[-1])

        bars_since_high = len(df) - 1 - high_pos
        if not cfg.min_dip_bars <= bars_since_high <= cfg.max_dip_bars:
            return None

        drawdown = (recent_high - close) / recent_high
        if not cfg.min_dip <= drawdown <= cfg.max_dip:
            return None

        pullback = df.iloc[high_pos:]
        atr_at_high = float(ind.atr14.iloc[high_pos]) if not pd.isna(ind.atr14.iloc[high_pos]) else 0.0
        avg_vol = float(ind.avg_volume.iloc[high_pos]) if not pd.isna(ind.avg_volume.iloc[high_pos]) else 0.0

        # Geordnet: kein Panik-Tag, kein extremer Volumen-Spike an Abwärtstagen.
        daily_moves = pullback["close"].diff().dropna()
        worst_drop = float(-daily_moves.min()) if not daily_moves.empty else 0.0
        no_panic_move = atr_at_high <= 0 or worst_drop <= cfg.panic_atr_mult * atr_at_high

        down_days = pullback[pullback["close"] < pullback["open"]]
        up_days = pullback[pullback["close"] >= pullback["open"]]
        max_down_volume = float(down_days["volume"].max()) if not down_days.empty else 0.0
        no_volume_spike = avg_vol <= 0 or max_down_volume <= cfg.volume_spike_limit * avg_vol
        orderly = no_panic_move and no_volume_spike

        # Verkaufsdruck-Kennzahl: Abwärts- vs. Aufwärts-Volumen (kleiner = besser).
        avg_down_vol = float(down_days["volume"].mean()) if not down_days.empty else 0.0
        avg_up_vol = float(up_days["volume"].mean()) if not up_days.empty else avg_vol or 1.0
        down_volume_ratio = avg_down_vol / avg_up_vol if avg_up_vol > 0 else 1.0

        return high_pos, recent_high, drawdown, orderly, down_volume_ratio

    # ------------------------------------------------------------------
    # 3. Unterstützung
    # ------------------------------------------------------------------

    def _nearest_support(
        self,
        df: pd.DataFrame,
        ind: _Indicators,
        high_pos: int,
        recent_high: float,
        close: float,
    ) -> tuple[SupportType, float] | None:
        """Findet die nächstgelegene relevante Unterstützung unterhalb des Kurses."""
        cfg = self.config
        candidates: list[tuple[SupportType, float]] = []

        for support_type, series in (
            (SupportType.EMA20, ind.ema20),
            (SupportType.EMA50, ind.ema50),
            (SupportType.EMA100, ind.ema100),
            (SupportType.EMA200, ind.ema200),
        ):
            if series is None or pd.isna(series.iloc[-1]):
                continue
            level = float(series.iloc[-1])
            if 0 < level < recent_high:
                candidates.append((support_type, level))

        # Fibonacci-Retracements der Aufwärtsbewegung Schwungtief -> Hoch.
        lookback_start = max(0, high_pos - cfg.trend_lookback)
        swing_low = float(df["low"].iloc[lookback_start : high_pos + 1].min())
        move = recent_high - swing_low
        if move > 0:
            candidates.append((SupportType.FIB_382, recent_high - 0.382 * move))
            candidates.append((SupportType.FIB_500, recent_high - 0.500 * move))
            candidates.append((SupportType.FIB_618, recent_high - 0.618 * move))

        # Früheres Ausbruchsniveau: markantes Hoch deutlich vor dem jüngsten Hoch.
        breakout_window = df["high"].iloc[max(0, high_pos - 100) : max(0, high_pos - 10)]
        if len(breakout_window) >= 10:
            breakout_level = float(breakout_window.max())
            if 0 < breakout_level <= recent_high * 0.97:
                candidates.append((SupportType.BREAKOUT_LEVEL, breakout_level))

        best: tuple[SupportType, float] | None = None
        best_distance = float("inf")
        for support_type, level in candidates:
            distance = (close - level) / close
            if -cfg.support_undercut_tolerance <= distance <= cfg.support_max_distance:
                if abs(distance) < best_distance:
                    best = (support_type, level)
                    best_distance = abs(distance)
        return best

    # ------------------------------------------------------------------
    # 4. Stabilisierung & Status
    # ------------------------------------------------------------------

    def _stabilization_score(self, df: pd.DataFrame, ind: _Indicators, high_pos: int) -> float:
        """Stabilisierungsgrad in [0, 1] aus Kerzen-, RSI- und Volumensignalen."""
        signals = 0
        last = df.iloc[-1]
        prev = df.iloc[-2]

        if self._is_bullish_candle(last, prev):
            signals += 1

        rsi_now = float(ind.rsi14.iloc[-1]) if not pd.isna(ind.rsi14.iloc[-1]) else 50.0
        rsi_prev = float(ind.rsi14.iloc[-2]) if not pd.isna(ind.rsi14.iloc[-2]) else 50.0
        if rsi_now > rsi_prev and rsi_prev <= 50.0:
            signals += 1

        # Kaufvolumen zieht an: letzter Aufwärtstag über dem Abwärtsschnitt des Rücksetzers.
        pullback = df.iloc[high_pos:]
        down_days = pullback[pullback["close"] < pullback["open"]]
        avg_down_vol = float(down_days["volume"].mean()) if not down_days.empty else 0.0
        if float(last["close"]) >= float(last["open"]) and float(last["volume"]) > avg_down_vol:
            signals += 1

        return signals / 3.0

    @staticmethod
    def _is_bullish_candle(last: pd.Series, prev: pd.Series) -> bool:
        """Hammer, Bullish Engulfing oder starker Schluss im oberen Bereich."""
        o, h, l, c = (float(last[k]) for k in ("open", "high", "low", "close"))
        candle_range = h - l
        if candle_range <= 0:
            return False
        body = abs(c - o)
        lower_wick = min(o, c) - l
        close_position = (c - l) / candle_range

        hammer = body > 0 and lower_wick >= 2.0 * body and close_position >= 0.6
        engulfing = (
            c > o
            and float(prev["close"]) < float(prev["open"])
            and c >= float(prev["open"])
            and o <= float(prev["close"])
        )
        strong_close = c > o and close_position >= 0.7
        return hammer or engulfing or strong_close

    def _determine_status(
        self, df: pd.DataFrame, ind: _Indicators, stabilization: float
    ) -> SetupStatus:
        """WATCHING -> CONFIRMED -> ENTRY anhand von Stabilisierung und Mikro-Breakout."""
        close = float(df["close"].iloc[-1])
        micro_high = float(df["high"].iloc[-4:-1].max())
        ema20_now = float(ind.ema20.iloc[-1]) if not pd.isna(ind.ema20.iloc[-1]) else close

        if stabilization >= 1.0 / 3.0 and close > micro_high and close > ema20_now:
            return SetupStatus.ENTRY
        if stabilization >= 2.0 / 3.0:
            return SetupStatus.CONFIRMED
        return SetupStatus.WATCHING

    # ------------------------------------------------------------------
    # Relative Stärke
    # ------------------------------------------------------------------

    def _relative_strength(self, df: pd.DataFrame, benchmark: pd.DataFrame | None) -> float:
        """Outperformance vs. Benchmark über ``rs_lookback`` Kerzen (Bruchteil)."""
        lookback = self.config.rs_lookback
        if len(df) <= lookback:
            return 0.0
        stock_return = float(df["close"].iloc[-1]) / float(df["close"].iloc[-lookback]) - 1.0
        if benchmark is None or len(benchmark) <= lookback:
            return stock_return
        bench_return = (
            float(benchmark["close"].iloc[-1]) / float(benchmark["close"].iloc[-lookback]) - 1.0
        )
        return stock_return - bench_return
