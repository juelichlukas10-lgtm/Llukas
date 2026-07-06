"""Unit-Tests für Performance-Metriken, Backtest-Engine und Optimierer."""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from tradingbot.analytics.performance import (
    build_report,
    drawdown_series,
    max_drawdown,
    sharpe_ratio,
)
from tradingbot.backtesting.engine import BacktestEngine, BacktestSettings
from tradingbot.backtesting.optimizer import (
    GridSearchOptimizer,
    RandomSearchOptimizer,
    WalkForwardAnalyzer,
)
from tradingbot.core.config import RiskConfig, SizingConfig
from tradingbot.core.enums import PositionSide, SizingMethod, Timeframe
from tradingbot.core.exceptions import BacktestError
from tradingbot.core.models import Trade
from tradingbot.strategies.ema_crossover import EmaCrossoverStrategy


def _trade(pnl: float, fees: float = 0.1) -> Trade:
    now = datetime.now(timezone.utc)
    return Trade(
        symbol="BTC/USDT",
        side=PositionSide.LONG,
        amount=1.0,
        entry_price=100.0,
        exit_price=100.0 + pnl,
        pnl=pnl,
        fees=fees,
        strategy="test",
        opened_at=now,
        closed_at=now,
    )


def _trending_data(n: int = 600, seed: int = 7) -> dict[str, pd.DataFrame]:
    """Erzeugt Daten mit klaren Trendwechseln für Crossover-Strategien."""
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    base = 100.0 + 20.0 * np.sin(t / 40.0) + 0.01 * t
    close = base + rng.normal(0, 0.4, n)
    open_ = np.concatenate(([close[0]], close[:-1]))
    high = np.maximum(open_, close) + np.abs(rng.normal(0, 0.2, n))
    low = np.minimum(open_, close) - np.abs(rng.normal(0, 0.2, n))
    volume = rng.uniform(80, 120, n)
    index = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    return {
        "BTC/USDT": pd.DataFrame(
            {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
            index=index,
        )
    }


class TestPerformanceMetrics:
    def test_report_basic_numbers(self) -> None:
        trades = [_trade(10.0), _trade(-5.0), _trade(20.0), _trade(-5.0)]
        equity = pd.Series(
            [10_000, 10_010, 10_005, 10_025, 10_020],
            index=pd.date_range("2024-01-01", periods=5, freq="1D", tz="UTC"),
        )
        report = build_report(trades, equity, 10_000.0)
        assert report.trade_count == 4
        assert report.win_count == 2
        assert report.win_rate == pytest.approx(0.5)
        assert report.total_pnl == pytest.approx(20.0)
        assert report.gross_profit == pytest.approx(30.0)
        assert report.gross_loss == pytest.approx(-10.0)
        assert report.profit_factor == pytest.approx(3.0)
        assert report.average_win == pytest.approx(15.0)
        assert report.average_loss == pytest.approx(-5.0)
        assert report.risk_reward_ratio == pytest.approx(3.0)
        assert report.expectancy == pytest.approx(0.5 * 15.0 + 0.5 * -5.0)
        assert report.total_fees == pytest.approx(0.4)

    def test_max_drawdown(self) -> None:
        equity = pd.Series([100.0, 120.0, 90.0, 110.0, 80.0])
        assert max_drawdown(equity) == pytest.approx(1.0 - 80.0 / 120.0)

    def test_drawdown_series_zero_at_peak(self) -> None:
        equity = pd.Series([100.0, 110.0, 105.0])
        dd = drawdown_series(equity)
        assert dd.iloc[0] == pytest.approx(0.0)
        assert dd.iloc[1] == pytest.approx(0.0)
        assert dd.iloc[2] == pytest.approx(1.0 - 105.0 / 110.0)

    def test_sharpe_positive_for_rising_equity(self) -> None:
        rng = np.random.default_rng(1)
        values = 10_000 * np.cumprod(1 + rng.normal(0.001, 0.002, 200))
        equity = pd.Series(values, index=pd.date_range("2024-01-01", periods=200, freq="1D", tz="UTC"))
        assert sharpe_ratio(equity) > 0

    def test_empty_trades_report(self) -> None:
        report = build_report([], pd.Series(dtype=float), 10_000.0)
        assert report.trade_count == 0
        assert report.win_rate == 0.0
        assert report.to_dict()["profit_factor"] == 0.0


class TestBacktestEngine:
    def _engine(self, **risk_overrides) -> BacktestEngine:
        settings = BacktestSettings(
            initial_balance=10_000.0,
            commission_rate=0.001,
            slippage_rate=0.0005,
            spread_rate=0.0002,
        )
        risk = RiskConfig(**risk_overrides) if risk_overrides else RiskConfig(
            stop_loss=0.05, take_profit=0.10, max_daily_trades=1000
        )
        sizing = SizingConfig(method=SizingMethod.PERCENT_RISK)
        return BacktestEngine(settings, risk, sizing)

    def test_run_produces_trades_and_equity(self) -> None:
        engine = self._engine()
        strategy = EmaCrossoverStrategy(
            symbols=["BTC/USDT"], timeframe=Timeframe.H1,
            params={"fast_period": 8, "slow_period": 21},
        )
        result = engine.run(strategy, _trending_data())

        assert result.report.trade_count > 0
        assert not result.equity_curve.empty
        assert result.equity_curve.iloc[0] == pytest.approx(10_000.0, rel=0.01)
        # Equity muss konsistent zum PnL sein.
        assert result.equity_curve.iloc[-1] == pytest.approx(
            10_000.0 + result.report.total_pnl, rel=0.02
        )
        assert all(t.fees > 0 for t in result.trades)

    def test_costs_reduce_pnl(self) -> None:
        data = _trending_data()
        strategy_params = {"fast_period": 8, "slow_period": 21}

        cheap = BacktestEngine(
            BacktestSettings(commission_rate=0.0, slippage_rate=0.0, spread_rate=0.0),
            RiskConfig(stop_loss=0.05, take_profit=0.10, max_daily_trades=1000),
        ).run(
            EmaCrossoverStrategy(["BTC/USDT"], Timeframe.H1, strategy_params), data
        )
        expensive = BacktestEngine(
            BacktestSettings(commission_rate=0.005, slippage_rate=0.002, spread_rate=0.002),
            RiskConfig(stop_loss=0.05, take_profit=0.10, max_daily_trades=1000),
        ).run(
            EmaCrossoverStrategy(["BTC/USDT"], Timeframe.H1, strategy_params), data
        )
        assert expensive.report.total_pnl < cheap.report.total_pnl

    def test_stop_loss_enforced(self) -> None:
        # Enger 0.5%-Stop: wird bei ~0.4% Kerzen-Rauschen sicher ausgelöst,
        # bevor ein EMA-Signal-Exit greifen kann.
        engine = self._engine(stop_loss=0.005, take_profit=0.0, max_daily_trades=1000)
        strategy = EmaCrossoverStrategy(
            symbols=["BTC/USDT"], timeframe=Timeframe.H1,
            params={"fast_period": 8, "slow_period": 21},
        )
        result = engine.run(strategy, _trending_data())
        stop_trades = [t for t in result.trades if t.exit_reason == "stop_loss"]
        assert stop_trades, "Es sollten Stop-Loss-Exits vorkommen"
        for trade in stop_trades:
            # Verlust darf ~0.5% (plus Gap/Slippage-Toleranz) nicht wesentlich überschreiten.
            loss_pct = (trade.entry_price - trade.exit_price) / trade.entry_price
            assert loss_pct <= 0.02

    def test_missing_data_raises(self) -> None:
        engine = self._engine()
        strategy = EmaCrossoverStrategy(symbols=["ETH/USDT"], timeframe=Timeframe.H1)
        with pytest.raises(BacktestError, match="Keine Daten"):
            engine.run(strategy, _trending_data())

    def test_multi_asset(self) -> None:
        data = _trending_data()
        eth = data["BTC/USDT"].copy()
        eth[["open", "high", "low", "close"]] *= 0.05
        data["ETH/USDT"] = eth
        engine = self._engine()
        strategy = EmaCrossoverStrategy(
            symbols=["BTC/USDT", "ETH/USDT"], timeframe=Timeframe.H1,
            params={"fast_period": 8, "slow_period": 21},
        )
        result = engine.run(strategy, data)
        traded_symbols = {t.symbol for t in result.trades}
        assert traded_symbols == {"BTC/USDT", "ETH/USDT"}


class TestOptimizers:
    def _engine(self) -> BacktestEngine:
        return BacktestEngine(
            BacktestSettings(initial_balance=10_000.0),
            RiskConfig(stop_loss=0.05, take_profit=0.10, max_daily_trades=1000),
        )

    def test_grid_search(self) -> None:
        optimizer = GridSearchOptimizer(
            self._engine(), EmaCrossoverStrategy, ["BTC/USDT"], Timeframe.H1,
            metric="total_pnl", min_trades=1,
        )
        result = optimizer.optimize(
            {"fast_period": [5, 8], "slow_period": [21, 34]}, _trending_data()
        )
        assert len(result.results) == 4
        assert result.best_params["fast_period"] in (5, 8)
        scores = [score for _, score, _ in result.results]
        assert result.best_score == max(scores)
        df = result.to_dataframe()
        assert len(df) == 4 and "total_pnl" in df.columns

    def test_random_search_with_ranges(self) -> None:
        optimizer = RandomSearchOptimizer(
            self._engine(), EmaCrossoverStrategy, ["BTC/USDT"], Timeframe.H1,
            metric="total_pnl", min_trades=1,
        )
        result = optimizer.optimize(
            {"fast_period": (3, 10), "slow_period": [21, 34]},
            _trending_data(),
            n_iterations=5,
            seed=42,
        )
        assert 1 <= len(result.results) <= 5
        assert 3 <= result.best_params["fast_period"] <= 10

    def test_walk_forward(self) -> None:
        analyzer = WalkForwardAnalyzer(
            self._engine(), EmaCrossoverStrategy, ["BTC/USDT"], Timeframe.H1,
            metric="total_pnl", min_trades=0,
        )
        result = analyzer.analyze(
            {"fast_period": [5, 8], "slow_period": [21]},
            _trending_data(n=800),
            n_windows=2,
            train_ratio=0.7,
        )
        assert len(result.windows) == 2
        assert not result.oos_equity.empty
        assert "sharpe_ratio" in result.oos_metrics

    def test_invalid_metric_raises(self) -> None:
        optimizer = GridSearchOptimizer(
            self._engine(), EmaCrossoverStrategy, ["BTC/USDT"], Timeframe.H1,
            metric="does_not_exist", min_trades=0,
        )
        with pytest.raises(BacktestError):
            optimizer.optimize({"fast_period": [8], "slow_period": [21]}, _trending_data())
