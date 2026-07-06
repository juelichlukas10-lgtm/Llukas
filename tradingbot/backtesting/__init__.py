"""Backtesting: Event-basierte Engine und Parameter-Optimierung."""

from tradingbot.backtesting.engine import BacktestEngine, BacktestResult, BacktestSettings
from tradingbot.backtesting.optimizer import (
    GridSearchOptimizer,
    OptimizationResult,
    RandomSearchOptimizer,
    WalkForwardAnalyzer,
    WalkForwardResult,
)

__all__ = [
    "BacktestEngine",
    "BacktestResult",
    "BacktestSettings",
    "GridSearchOptimizer",
    "OptimizationResult",
    "RandomSearchOptimizer",
    "WalkForwardAnalyzer",
    "WalkForwardResult",
]
