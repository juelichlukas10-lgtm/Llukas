"""Parameter-Optimierung: Grid Search, Random Search, Walk-Forward.

Alle Optimierer arbeiten auf der :class:`BacktestEngine` und bewerten
Parameterkombinationen anhand einer wählbaren Zielmetrik (z. B.
``sharpe_ratio``, ``total_pnl``, ``profit_factor``).
"""

from __future__ import annotations

import itertools
import random
from dataclasses import dataclass, field
from typing import Any, Type

import pandas as pd

from tradingbot.backtesting.engine import BacktestEngine, BacktestResult
from tradingbot.core.enums import Timeframe
from tradingbot.core.exceptions import BacktestError
from tradingbot.core.logging import get_logger
from tradingbot.strategies.base import Strategy

logger = get_logger(__name__)

#: Metriken, bei denen kleinere Werte besser sind.
_MINIMIZE_METRICS = {"max_drawdown"}


@dataclass(slots=True)
class OptimizationResult:
    """Ergebnis einer Parameter-Optimierung.

    Attributes:
        best_params: Beste gefundene Parameterkombination.
        best_score: Zielmetrik-Wert der besten Kombination.
        metric: Verwendete Zielmetrik.
        results: Alle Läufe als Liste von (Params, Score, BacktestResult).
    """

    best_params: dict[str, Any]
    best_score: float
    metric: str
    results: list[tuple[dict[str, Any], float, BacktestResult]] = field(default_factory=list)

    def to_dataframe(self) -> pd.DataFrame:
        """Alle Läufe als DataFrame (eine Zeile pro Kombination)."""
        rows = []
        for params, score, result in self.results:
            row: dict[str, Any] = dict(params)
            row[self.metric] = score
            row["trade_count"] = result.report.trade_count
            row["total_pnl"] = result.report.total_pnl
            row["max_drawdown"] = result.report.max_drawdown
            rows.append(row)
        return pd.DataFrame(rows)


def _score(result: BacktestResult, metric: str) -> float:
    """Extrahiert die Zielmetrik aus einem Backtest-Ergebnis."""
    metrics = result.report.to_dict()
    if metric not in metrics:
        raise BacktestError(
            f"Unbekannte Zielmetrik '{metric}'. Verfügbar: {sorted(metrics)}"
        )
    value = metrics[metric]
    if value is None:
        return float("-inf")
    return float(value)


def _is_better(candidate: float, incumbent: float, metric: str) -> bool:
    """Vergleicht zwei Scores unter Berücksichtigung der Optimierungsrichtung."""
    if metric in _MINIMIZE_METRICS:
        return candidate < incumbent
    return candidate > incumbent


class _BaseOptimizer:
    """Gemeinsame Logik aller Optimierer."""

    def __init__(
        self,
        engine: BacktestEngine,
        strategy_class: Type[Strategy],
        symbols: list[str],
        timeframe: Timeframe,
        metric: str = "sharpe_ratio",
        min_trades: int = 5,
    ) -> None:
        self._engine = engine
        self._strategy_class = strategy_class
        self._symbols = symbols
        self._timeframe = timeframe
        self._metric = metric
        self._min_trades = min_trades

    def _evaluate(
        self, params: dict[str, Any], data: dict[str, pd.DataFrame]
    ) -> tuple[float, BacktestResult] | None:
        """Führt einen Lauf aus; None bei Fehlern oder zu wenigen Trades."""
        try:
            strategy = self._strategy_class(
                symbols=self._symbols, timeframe=self._timeframe, params=params
            )
            result = self._engine.run(strategy, data)
        except Exception as exc:
            logger.warning("Kombination %s fehlgeschlagen: %s", params, exc)
            return None
        if result.report.trade_count < self._min_trades:
            worst = float("inf") if self._metric in _MINIMIZE_METRICS else float("-inf")
            return worst, result
        return _score(result, self._metric), result

    def _run_combinations(
        self, combinations: list[dict[str, Any]], data: dict[str, pd.DataFrame]
    ) -> OptimizationResult:
        """Bewertet alle Kombinationen und ermittelt die beste."""
        if not combinations:
            raise BacktestError("Keine Parameterkombinationen zu testen")
        best_params: dict[str, Any] | None = None
        best_score = float("inf") if self._metric in _MINIMIZE_METRICS else float("-inf")
        all_results: list[tuple[dict[str, Any], float, BacktestResult]] = []

        for i, params in enumerate(combinations, start=1):
            evaluated = self._evaluate(params, data)
            if evaluated is None:
                continue
            score, result = evaluated
            all_results.append((params, score, result))
            if best_params is None or _is_better(score, best_score, self._metric):
                best_params, best_score = params, score
            logger.debug(
                "Optimierung %d/%d: %s -> %s=%.4f", i, len(combinations), params, self._metric, score
            )

        if best_params is None:
            raise BacktestError("Alle Parameterkombinationen sind fehlgeschlagen")
        logger.info(
            "Optimierung abgeschlossen: beste %s=%.4f mit %s", self._metric, best_score, best_params
        )
        return OptimizationResult(
            best_params=best_params, best_score=best_score, metric=self._metric, results=all_results
        )


class GridSearchOptimizer(_BaseOptimizer):
    """Erschöpfende Suche über das kartesische Produkt aller Parameterwerte.

    Beispiel:
        >>> optimizer = GridSearchOptimizer(engine, EmaCrossoverStrategy,
        ...                                 ["BTC/USDT"], Timeframe.H1)
        >>> result = optimizer.optimize(
        ...     {"fast_period": [8, 12, 16], "slow_period": [21, 26, 34]}, data
        ... )
    """

    def optimize(
        self, param_grid: dict[str, list[Any]], data: dict[str, pd.DataFrame]
    ) -> OptimizationResult:
        """Testet alle Kombinationen des Parameter-Gitters.

        Args:
            param_grid: Mapping Parameter -> Liste möglicher Werte.
            data: OHLCV-Daten je Symbol.
        """
        if not param_grid:
            raise BacktestError("param_grid darf nicht leer sein")
        keys = list(param_grid)
        combinations = [
            dict(zip(keys, values)) for values in itertools.product(*(param_grid[k] for k in keys))
        ]
        logger.info("Grid Search: %d Kombinationen", len(combinations))
        return self._run_combinations(combinations, data)


class RandomSearchOptimizer(_BaseOptimizer):
    """Zufällige Stichprobe aus dem Parameterraum.

    Unterstützt diskrete Listen sowie (min, max)-Bereiche für ints/floats.
    """

    def optimize(
        self,
        param_space: dict[str, list[Any] | tuple[float, float]],
        data: dict[str, pd.DataFrame],
        n_iterations: int = 50,
        seed: int | None = None,
    ) -> OptimizationResult:
        """Zieht ``n_iterations`` zufällige Kombinationen und bewertet sie.

        Args:
            param_space: Parameter -> Werteliste oder (min, max)-Bereich.
                Bereiche aus zwei ints erzeugen ints, sonst floats.
            data: OHLCV-Daten je Symbol.
            n_iterations: Anzahl der Stichproben.
            seed: Zufalls-Seed für Reproduzierbarkeit.
        """
        if not param_space:
            raise BacktestError("param_space darf nicht leer sein")
        rng = random.Random(seed)
        combinations: list[dict[str, Any]] = []
        seen: set[tuple[Any, ...]] = set()
        keys = sorted(param_space)

        for _ in range(n_iterations * 5):  # Oversampling wegen Duplikaten
            if len(combinations) >= n_iterations:
                break
            params: dict[str, Any] = {}
            for key in keys:
                space = param_space[key]
                if isinstance(space, tuple) and len(space) == 2:
                    low, high = space
                    if isinstance(low, int) and isinstance(high, int):
                        params[key] = rng.randint(low, high)
                    else:
                        params[key] = rng.uniform(float(low), float(high))
                elif isinstance(space, list) and space:
                    params[key] = rng.choice(space)
                else:
                    raise BacktestError(f"Ungültiger Parameterraum für '{key}': {space!r}")
            fingerprint = tuple(params[k] for k in keys)
            if fingerprint not in seen:
                seen.add(fingerprint)
                combinations.append(params)

        logger.info("Random Search: %d Kombinationen (angefordert: %d)", len(combinations), n_iterations)
        return self._run_combinations(combinations, data)


@dataclass(slots=True)
class WalkForwardResult:
    """Ergebnis einer Walk-Forward-Analyse.

    Attributes:
        windows: Je Fenster (train_result, test_result, best_params).
        oos_equity: Zusammengesetzte Out-of-Sample-Equity-Kurve.
        oos_metrics: Kennzahlen über alle Out-of-Sample-Abschnitte.
    """

    windows: list[tuple[OptimizationResult, BacktestResult, dict[str, Any]]]
    oos_equity: pd.Series
    oos_metrics: dict[str, Any]


class WalkForwardAnalyzer(_BaseOptimizer):
    """Walk-Forward-Analyse: rollierende Optimierung + Out-of-Sample-Test.

    Die Daten werden in ``n_windows`` aufeinanderfolgende Fenster geteilt.
    In jedem Fenster wird auf dem Trainingsanteil optimiert und mit den
    besten Parametern auf dem anschließenden Testanteil validiert.
    """

    def analyze(
        self,
        param_grid: dict[str, list[Any]],
        data: dict[str, pd.DataFrame],
        n_windows: int = 4,
        train_ratio: float = 0.75,
    ) -> WalkForwardResult:
        """Führt die Walk-Forward-Analyse aus.

        Args:
            param_grid: Parameter-Gitter für die Optimierung je Fenster.
            data: OHLCV-Daten je Symbol.
            n_windows: Anzahl der Walk-Forward-Fenster.
            train_ratio: Anteil des Fensters für das Training in (0, 1).

        Returns:
            :class:`WalkForwardResult` mit Fenster-Details und
            zusammengesetzter Out-of-Sample-Bewertung.

        Raises:
            BacktestError: Bei ungültigen Parametern oder zu wenig Daten.
        """
        if not 0 < train_ratio < 1:
            raise BacktestError(f"train_ratio muss in (0, 1) liegen: {train_ratio}")
        if n_windows < 1:
            raise BacktestError("n_windows muss >= 1 sein")

        # Gemeinsame Zeitachse bestimmen.
        common_index = sorted(set().union(*[set(df.index) for df in data.values()]))
        total = len(common_index)
        window_size = total // n_windows
        if window_size < 50:
            raise BacktestError(
                f"Zu wenig Daten für {n_windows} Fenster ({total} Kerzen gesamt)"
            )

        windows: list[tuple[OptimizationResult, BacktestResult, dict[str, Any]]] = []
        oos_trades = []
        oos_equity_parts: list[pd.Series] = []

        for w in range(n_windows):
            start_idx = w * window_size
            end_idx = total if w == n_windows - 1 else (w + 1) * window_size
            split_idx = start_idx + int((end_idx - start_idx) * train_ratio)

            train_range = (common_index[start_idx], common_index[split_idx - 1])
            test_range = (common_index[split_idx], common_index[end_idx - 1])

            train_data = {
                s: df[(df.index >= train_range[0]) & (df.index <= train_range[1])]
                for s, df in data.items()
            }
            test_data = {
                s: df[(df.index >= test_range[0]) & (df.index <= test_range[1])]
                for s, df in data.items()
            }

            logger.info(
                "Walk-Forward Fenster %d/%d: Training %s..%s, Test %s..%s",
                w + 1, n_windows, train_range[0], train_range[1], test_range[0], test_range[1],
            )
            grid = GridSearchOptimizer(
                self._engine,
                self._strategy_class,
                self._symbols,
                self._timeframe,
                metric=self._metric,
                min_trades=self._min_trades,
            )
            train_result = grid.optimize(param_grid, train_data)

            test_strategy = self._strategy_class(
                symbols=self._symbols, timeframe=self._timeframe, params=train_result.best_params
            )
            test_result = self._engine.run(test_strategy, test_data)
            windows.append((train_result, test_result, train_result.best_params))
            oos_trades.extend(test_result.trades)
            if not test_result.equity_curve.empty:
                oos_equity_parts.append(test_result.equity_curve)

        # Out-of-Sample-Kurve normalisiert zusammensetzen (Renditen verketten).
        if oos_equity_parts:
            chained: list[pd.Series] = []
            level = float(oos_equity_parts[0].iloc[0])
            for part in oos_equity_parts:
                returns = part / part.iloc[0]
                chained.append(returns * level)
                level = float(chained[-1].iloc[-1])
            oos_equity = pd.concat(chained)
            oos_equity = oos_equity[~oos_equity.index.duplicated(keep="last")].sort_index()
        else:
            oos_equity = pd.Series(dtype=float)

        from tradingbot.analytics.performance import build_report

        initial = float(oos_equity.iloc[0]) if not oos_equity.empty else 0.0
        oos_report = build_report(oos_trades, oos_equity, initial or 1.0)
        logger.info(
            "Walk-Forward abgeschlossen: OOS-Trades %d, OOS-Sharpe %.2f",
            oos_report.trade_count,
            oos_report.sharpe_ratio,
        )
        return WalkForwardResult(
            windows=windows, oos_equity=oos_equity, oos_metrics=oos_report.to_dict()
        )
