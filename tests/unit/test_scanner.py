"""Unit-Tests für den Buy-the-Dip-Marktscanner."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tradingbot.core.config import Config
from tradingbot.core.exceptions import ConfigError
from tradingbot.database.repository import Database
from tradingbot.scanner.data_provider import StockDataProvider
from tradingbot.scanner.detector import DetectorConfig, DipDetector
from tradingbot.scanner.engine import ScannerEngine
from tradingbot.scanner.models import SetupStatus
from tradingbot.scanner.scoring import DipScorer, ScoreFactors
from tradingbot.scanner.universe import BUILTIN_UNIVERSES, load_universe


# ----------------------------------------------------------------------
# Synthetische Kursverläufe
# ----------------------------------------------------------------------


def _make_df(closes: np.ndarray, volumes: np.ndarray | None = None) -> pd.DataFrame:
    """Baut ein Tages-OHLCV-Frame aus Schlusskursen (geringe Intraday-Spanne)."""
    n = len(closes)
    opens = np.concatenate(([closes[0]], closes[:-1]))
    highs = np.maximum(opens, closes) * 1.004
    lows = np.minimum(opens, closes) * 0.996
    vols = volumes if volumes is not None else np.full(n, 1_000_000.0)
    index = pd.date_range("2025-01-01", periods=n, freq="1D", tz="UTC")
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": vols},
        index=index,
    )


def make_dip_setup(
    n_trend: int = 150,
    dip_pct: float = 0.07,
    dip_bars: int = 8,
    stabilize: bool = True,
    panic: bool = False,
    volumes_dip_factor: float = 0.8,
) -> pd.DataFrame:
    """Erzeugt einen klaren Aufwärtstrend mit anschließendem Rücksetzer.

    Args:
        n_trend: Länge der Trendphase in Tagen.
        dip_pct: Tiefe des Rücksetzers vom Hoch.
        dip_bars: Dauer des Rücksetzers in Tagen.
        stabilize: Letzte Kerze als bullische Stabilisierung formen.
        panic: Rücksetzer als Panik-Abverkauf (ein großer Tagesverlust).
        volumes_dip_factor: Volumen im Rücksetzer relativ zur Trendphase.
    """
    rng = np.random.default_rng(7)
    # Stetiger Aufwärtstrend: +40% über die Trendphase, mildes Rauschen.
    trend = 100.0 * np.cumprod(1 + np.full(n_trend, 0.4 / n_trend) + rng.normal(0, 0.002, n_trend))
    high = trend[-1]

    if panic:
        # Gesamter Rücksetzer in einem einzigen Crash-Tag, Rest seitwärts.
        dip_path = [high * (1 - dip_pct)] * dip_bars
    else:
        # Gleichmäßig verteilter, geordneter Rücksetzer.
        steps = np.linspace(0, dip_pct, dip_bars + 1)[1:]
        dip_path = [high * (1 - s) for s in steps]

    closes = list(trend) + dip_path
    if stabilize:
        # Bullische Schlusskerze: dreht vom Tief leicht nach oben.
        closes.append(closes[-1] * 1.012)

    closes_arr = np.array(closes)
    volumes = np.full(len(closes_arr), 1_000_000.0)
    volumes[n_trend:] *= volumes_dip_factor
    if stabilize:
        volumes[-1] = 1_200_000.0  # Kaufvolumen zieht an
    return _make_df(closes_arr, volumes)


def make_downtrend(n: int = 200) -> pd.DataFrame:
    """Klarer Abwärtstrend (darf niemals ein Setup liefern)."""
    closes = 100.0 * np.cumprod(np.full(n, 1 - 0.15 / n))
    return _make_df(closes)


def make_benchmark(n: int = 200) -> pd.DataFrame:
    """Flache Benchmark (Markt seitwärts)."""
    return _make_df(np.full(n, 100.0))


# ----------------------------------------------------------------------
# Detektor
# ----------------------------------------------------------------------


class TestDipDetector:
    def _detector(self, **overrides) -> DipDetector:
        return DipDetector(DetectorConfig(**overrides))

    def test_detects_clean_dip(self) -> None:
        df = make_dip_setup()
        signal = self._detector().detect("TEST", "Test AG", df, make_benchmark(len(df)))
        assert signal is not None
        assert signal.symbol == "TEST"
        assert 0.03 <= signal.drawdown_pct <= 0.20
        assert signal.status in (SetupStatus.WATCHING, SetupStatus.CONFIRMED, SetupStatus.ENTRY)
        assert signal.stop_loss < signal.price
        assert signal.target_1 > signal.entry_price > signal.stop_loss
        assert signal.risk_reward > 0
        assert 0 <= signal.score <= 100

    def test_rejects_downtrend(self) -> None:
        df = make_downtrend()
        assert self._detector().detect("DOWN", "Down AG", df, None) is None

    def test_rejects_no_dip(self) -> None:
        # Reiner Aufwärtstrend ohne Rücksetzer.
        df = make_dip_setup(dip_pct=0.005, dip_bars=3, stabilize=False)
        assert self._detector().detect("FLAT", "Flat AG", df, None) is None

    def test_rejects_too_deep_dip(self) -> None:
        df = make_dip_setup(dip_pct=0.35, dip_bars=20, stabilize=False)
        assert self._detector().detect("DEEP", "Deep AG", df, None) is None

    def test_panic_selloff_scores_lower(self) -> None:
        orderly = self._detector().detect(
            "A", "A", make_dip_setup(dip_pct=0.07, dip_bars=8), None
        )
        panic = self._detector(panic_atr_mult=1.5).detect(
            "B", "B", make_dip_setup(dip_pct=0.07, dip_bars=8, panic=True), None
        )
        assert orderly is not None
        # Panik-Setup ist entweder verworfen oder deutlich schlechter bewertet.
        if panic is not None:
            assert panic.score < orderly.score

    def test_stabilization_upgrades_status(self) -> None:
        without = self._detector().detect(
            "A", "A", make_dip_setup(stabilize=False), None
        )
        with_stab = self._detector().detect(
            "B", "B", make_dip_setup(stabilize=True), None
        )
        assert with_stab is not None
        rank = {SetupStatus.WATCHING: 0, SetupStatus.CONFIRMED: 1, SetupStatus.ENTRY: 2}
        if without is not None:
            assert rank[with_stab.status] >= rank[without.status]

    def test_insufficient_history_rejected(self) -> None:
        df = make_dip_setup(n_trend=40, dip_bars=5)
        assert self._detector().detect("SHORT", "Short AG", df, None) is None

    def test_relative_strength_vs_benchmark(self) -> None:
        df = make_dip_setup()
        detector = self._detector()
        signal = detector.detect("RS", "RS AG", df, make_benchmark(len(df)))
        assert signal is not None
        # Aktie +40% im Trend, Benchmark flach -> deutliche Outperformance.
        assert signal.relative_strength > 0.05


# ----------------------------------------------------------------------
# Scoring
# ----------------------------------------------------------------------


class TestScoring:
    def _factors(self, **overrides) -> ScoreFactors:
        defaults = dict(
            trend_strength=0.8,
            drawdown_pct=0.07,
            support_distance_pct=0.01,
            orderly=True,
            down_volume_ratio=0.7,
            stabilization=2 / 3,
            rsi=45.0,
            relative_strength=0.05,
            risk_reward=2.5,
            status=SetupStatus.CONFIRMED,
        )
        defaults.update(overrides)
        return ScoreFactors(**defaults)

    def test_score_in_range(self) -> None:
        score, breakdown = DipScorer().score(self._factors())
        assert 0 <= score <= 100
        assert breakdown  # Aufschlüsselung vorhanden

    def test_better_trend_scores_higher(self) -> None:
        scorer = DipScorer()
        weak, _ = scorer.score(self._factors(trend_strength=0.3))
        strong, _ = scorer.score(self._factors(trend_strength=1.0))
        assert strong > weak

    def test_orderly_beats_panic(self) -> None:
        scorer = DipScorer()
        orderly, _ = scorer.score(self._factors(orderly=True))
        panic, _ = scorer.score(self._factors(orderly=False))
        assert orderly > panic

    def test_entry_status_beats_watching(self) -> None:
        scorer = DipScorer()
        watching, _ = scorer.score(self._factors(status=SetupStatus.WATCHING))
        entry, _ = scorer.score(self._factors(status=SetupStatus.ENTRY))
        assert entry > watching

    def test_perfect_factors_near_100(self) -> None:
        score, _ = DipScorer().score(
            self._factors(
                trend_strength=1.0, drawdown_pct=0.07, support_distance_pct=0.0,
                down_volume_ratio=0.4, stabilization=1.0, relative_strength=0.15,
                risk_reward=5.0, status=SetupStatus.ENTRY,
            )
        )
        assert score >= 90


# ----------------------------------------------------------------------
# Universum
# ----------------------------------------------------------------------


class TestUniverse:
    def test_builtin_universes_exist(self) -> None:
        assert set(BUILTIN_UNIVERSES) == {
            "dow_jones", "nasdaq_100", "sp500", "eu_large", "international"
        }
        assert len(BUILTIN_UNIVERSES["dow_jones"]) == 30
        assert len(BUILTIN_UNIVERSES["sp500"]) >= 480

    def test_load_combines_and_dedupes(self) -> None:
        combined = load_universe(["dow_jones", "nasdaq_100"])
        # AAPL/MSFT sind in beiden enthalten -> keine Duplikate.
        assert len(combined) < 30 + len(BUILTIN_UNIVERSES["nasdaq_100"])
        assert "AAPL" in combined

    def test_custom_tickers(self) -> None:
        universe = load_universe(["dow_jones"], custom_tickers=["pltr", " COIN "])
        assert "PLTR" in universe
        assert "COIN" in universe

    def test_csv_loading(self, tmp_path) -> None:
        csv = tmp_path / "custom.csv"
        csv.write_text("symbol,name\nIWM,Russell 2000 ETF\nQQQ,Nasdaq ETF\n", encoding="utf-8")
        universe = load_universe([], csv_path=csv)
        assert universe == {"IWM": "Russell 2000 ETF", "QQQ": "Nasdaq ETF"}

    def test_unknown_universe_raises(self) -> None:
        with pytest.raises(ConfigError, match="Unbekanntes Universum"):
            load_universe(["does_not_exist"])

    def test_missing_csv_raises(self, tmp_path) -> None:
        with pytest.raises(ConfigError, match="nicht gefunden"):
            load_universe([], csv_path=tmp_path / "missing.csv")


# ----------------------------------------------------------------------
# Engine (mit Mock-Provider und In-Memory-Datenbank)
# ----------------------------------------------------------------------


class MockProvider(StockDataProvider):
    """Liefert vorgefertigte Frames statt echter Marktdaten."""

    def __init__(self, frames: dict[str, pd.DataFrame]) -> None:
        self.frames = frames

    async def fetch_history(self, symbols, period="1y", interval="1d"):
        return {s: self.frames[s] for s in symbols if s in self.frames}

    async def fetch_benchmark(self, period="1y"):
        return make_benchmark(200)


def _engine_config(tmp_path) -> Config:
    """Konfiguration mit Mini-Universum und In-Memory-SQLite."""
    return Config.model_validate(
        {
            "scanner": {
                "universes": [],
                "custom_tickers": ["GOOD", "BAD"],
                "interval_seconds": 60,
                "filters": {"min_price": 1.0, "min_avg_volume": 0, "min_score": 10.0},
            },
            "database": {"url": "sqlite:///:memory:"},
        }
    )


class TestScannerEngine:
    async def test_scan_once_finds_setup_and_persists(self, tmp_path) -> None:
        config = _engine_config(tmp_path)
        db = Database(url="sqlite:///:memory:")
        provider = MockProvider({"GOOD": make_dip_setup(), "BAD": make_downtrend()})
        engine = ScannerEngine(config, provider=provider, database=db)

        stats = await engine.scan_once()

        assert stats.scanned_symbols == 2
        assert stats.signals_found == 1
        signals = db.get_scanner_signals(active_only=True)
        assert len(signals) == 1
        assert signals[0].symbol == "GOOD"
        assert signals[0].score >= 10.0

        status = db.get_scanner_status()
        assert status is not None
        assert status.universe_size == 2
        db.close()

    async def test_disappearing_setup_marked_invalidated(self, tmp_path) -> None:
        config = _engine_config(tmp_path)
        db = Database(url="sqlite:///:memory:")
        provider = MockProvider({"GOOD": make_dip_setup(), "BAD": make_downtrend()})
        engine = ScannerEngine(config, provider=provider, database=db)
        await engine.scan_once()

        # Zweiter Zyklus: Setup verschwindet (Aktie jetzt im Abwärtstrend).
        provider.frames["GOOD"] = make_downtrend()
        await engine.scan_once()

        active = db.get_scanner_signals(active_only=True)
        assert active == []
        all_signals = db.get_scanner_signals(active_only=False)
        assert len(all_signals) == 1
        assert all_signals[0].status == "invalidated"
        db.close()

    async def test_detected_at_preserved_across_cycles(self, tmp_path) -> None:
        config = _engine_config(tmp_path)
        db = Database(url="sqlite:///:memory:")
        provider = MockProvider({"GOOD": make_dip_setup(), "BAD": make_downtrend()})
        engine = ScannerEngine(config, provider=provider, database=db)

        await engine.scan_once()
        first = db.get_scanner_signals()[0].detected_at
        await engine.scan_once()
        second = db.get_scanner_signals()[0].detected_at
        assert first == second  # Ersterkennung bleibt stabil
        db.close()

    async def test_min_score_filter(self, tmp_path) -> None:
        config = _engine_config(tmp_path)
        config.scanner.filters.min_score = 99.5  # praktisch unerreichbar
        db = Database(url="sqlite:///:memory:")
        provider = MockProvider({"GOOD": make_dip_setup(), "BAD": make_downtrend()})
        engine = ScannerEngine(config, provider=provider, database=db)

        stats = await engine.scan_once()
        assert stats.signals_found == 0
        db.close()
