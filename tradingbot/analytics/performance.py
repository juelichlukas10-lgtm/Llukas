"""Performance-Kennzahlen für Backtests und Live-Auswertung.

Berechnet alle gängigen Metriken aus einer Trade-Liste und einer
Equity-Kurve: Gewinn/Verlust, Trefferquote, Profit Factor, Sharpe,
Sortino, Calmar, Max Drawdown, Average Win/Loss, Risk-Reward,
Expectancy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from tradingbot.core.models import Trade

#: Annualisierungsfaktoren: Perioden pro Jahr je Equity-Frequenz.
PERIODS_PER_YEAR = 365.0


@dataclass(frozen=True, slots=True)
class PerformanceReport:
    """Vollständiger Kennzahlen-Bericht.

    Attributes:
        total_pnl: Netto-Gesamtgewinn in Quote-Währung.
        total_return: Gesamtrendite relativ zum Startkapital.
        gross_profit: Summe aller Gewinne.
        gross_loss: Summe aller Verluste (negativ).
        trade_count: Anzahl abgeschlossener Trades.
        win_count: Anzahl Gewinn-Trades.
        loss_count: Anzahl Verlust-Trades.
        win_rate: Trefferquote in [0, 1].
        profit_factor: Bruttogewinn / |Bruttoverlust| (inf bei 0 Verlust).
        average_win: Durchschnittlicher Gewinn-Trade.
        average_loss: Durchschnittlicher Verlust-Trade (negativ).
        risk_reward_ratio: |average_win / average_loss|.
        expectancy: Erwartungswert pro Trade in Quote-Währung.
        sharpe_ratio: Annualisierte Sharpe-Ratio der Equity-Renditen.
        sortino_ratio: Annualisierte Sortino-Ratio.
        calmar_ratio: Annualisierte Rendite / Max Drawdown.
        max_drawdown: Maximaler Drawdown als Bruchteil in [0, 1].
        total_fees: Summe aller Gebühren.
        equity_curve: Equity-Verlauf (Index = Zeit).
        drawdown_curve: Drawdown-Verlauf als Bruchteil.
    """

    total_pnl: float = 0.0
    total_return: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    trade_count: int = 0
    win_count: int = 0
    loss_count: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    average_win: float = 0.0
    average_loss: float = 0.0
    risk_reward_ratio: float = 0.0
    expectancy: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    max_drawdown: float = 0.0
    total_fees: float = 0.0
    equity_curve: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    drawdown_curve: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))

    def to_dict(self) -> dict[str, Any]:
        """Alle skalaren Kennzahlen als JSON-serialisierbares Dict."""
        return {
            "total_pnl": round(self.total_pnl, 8),
            "total_return": round(self.total_return, 6),
            "gross_profit": round(self.gross_profit, 8),
            "gross_loss": round(self.gross_loss, 8),
            "trade_count": self.trade_count,
            "win_count": self.win_count,
            "loss_count": self.loss_count,
            "win_rate": round(self.win_rate, 4),
            "profit_factor": round(self.profit_factor, 4) if np.isfinite(self.profit_factor) else None,
            "average_win": round(self.average_win, 8),
            "average_loss": round(self.average_loss, 8),
            "risk_reward_ratio": (
                round(self.risk_reward_ratio, 4) if np.isfinite(self.risk_reward_ratio) else None
            ),
            "expectancy": round(self.expectancy, 8),
            "sharpe_ratio": round(self.sharpe_ratio, 4),
            "sortino_ratio": round(self.sortino_ratio, 4) if np.isfinite(self.sortino_ratio) else None,
            "calmar_ratio": round(self.calmar_ratio, 4) if np.isfinite(self.calmar_ratio) else None,
            "max_drawdown": round(self.max_drawdown, 4),
            "total_fees": round(self.total_fees, 8),
        }


def drawdown_series(equity: pd.Series) -> pd.Series:
    """Drawdown-Verlauf einer Equity-Kurve als Bruchteil in [0, 1]."""
    if equity.empty:
        return pd.Series(dtype=float)
    running_max = equity.cummax()
    return 1.0 - equity / running_max


def max_drawdown(equity: pd.Series) -> float:
    """Maximaler Drawdown einer Equity-Kurve."""
    dd = drawdown_series(equity)
    return float(dd.max()) if not dd.empty else 0.0


def _annualization_factor(equity: pd.Series) -> float:
    """Perioden pro Jahr aus dem Median-Abstand des Zeitindex ableiten."""
    if not isinstance(equity.index, pd.DatetimeIndex) or len(equity) < 3:
        return PERIODS_PER_YEAR
    deltas = equity.index.to_series().diff().dropna()
    median_seconds = deltas.dt.total_seconds().median()
    if median_seconds <= 0:
        return PERIODS_PER_YEAR
    return 365.0 * 24 * 3600 / median_seconds


def sharpe_ratio(equity: pd.Series, risk_free_rate: float = 0.0) -> float:
    """Annualisierte Sharpe-Ratio der periodischen Equity-Renditen.

    Args:
        equity: Equity-Kurve mit DatetimeIndex.
        risk_free_rate: Risikofreier Jahreszins als Bruchteil.
    """
    if len(equity) < 3:
        return 0.0
    returns = equity.pct_change().dropna()
    if returns.empty or returns.std(ddof=1) == 0:
        return 0.0
    periods = _annualization_factor(equity)
    period_rf = risk_free_rate / periods
    excess = returns - period_rf
    return float(excess.mean() / excess.std(ddof=1) * np.sqrt(periods))


def sortino_ratio(equity: pd.Series, risk_free_rate: float = 0.0) -> float:
    """Annualisierte Sortino-Ratio (nur Downside-Volatilität im Nenner)."""
    if len(equity) < 3:
        return 0.0
    returns = equity.pct_change().dropna()
    if returns.empty:
        return 0.0
    periods = _annualization_factor(equity)
    period_rf = risk_free_rate / periods
    excess = returns - period_rf
    downside = excess[excess < 0]
    if downside.empty:
        return float("inf") if excess.mean() > 0 else 0.0
    downside_std = float(np.sqrt((downside**2).mean()))
    if downside_std == 0:
        return 0.0
    return float(excess.mean() / downside_std * np.sqrt(periods))


def calmar_ratio(equity: pd.Series) -> float:
    """Annualisierte Rendite geteilt durch den maximalen Drawdown."""
    if len(equity) < 3:
        return 0.0
    mdd = max_drawdown(equity)
    if mdd == 0:
        return float("inf") if equity.iloc[-1] > equity.iloc[0] else 0.0
    total_return = equity.iloc[-1] / equity.iloc[0] - 1.0
    if isinstance(equity.index, pd.DatetimeIndex):
        years = max(
            (equity.index[-1] - equity.index[0]).total_seconds() / (365.0 * 24 * 3600), 1e-9
        )
    else:
        years = max(len(equity) / PERIODS_PER_YEAR, 1e-9)
    annual_return = (1.0 + total_return) ** (1.0 / years) - 1.0
    return float(annual_return / mdd)


def build_report(
    trades: list[Trade],
    equity_curve: pd.Series,
    initial_balance: float,
) -> PerformanceReport:
    """Erstellt den vollständigen Kennzahlen-Bericht.

    Args:
        trades: Abgeschlossene Trades.
        equity_curve: Equity-Verlauf (Mark-to-Market, DatetimeIndex).
        initial_balance: Startkapital.

    Returns:
        :class:`PerformanceReport` mit allen Metriken.
    """
    pnls = np.array([t.pnl for t in trades], dtype=float)
    wins = pnls[pnls > 0]
    losses = pnls[pnls < 0]

    gross_profit = float(wins.sum()) if wins.size else 0.0
    gross_loss = float(losses.sum()) if losses.size else 0.0
    total_pnl = float(pnls.sum()) if pnls.size else 0.0
    win_rate = float(wins.size / pnls.size) if pnls.size else 0.0
    average_win = float(wins.mean()) if wins.size else 0.0
    average_loss = float(losses.mean()) if losses.size else 0.0
    profit_factor = (
        gross_profit / abs(gross_loss) if gross_loss != 0 else (float("inf") if gross_profit > 0 else 0.0)
    )
    risk_reward = abs(average_win / average_loss) if average_loss != 0 else (
        float("inf") if average_win > 0 else 0.0
    )
    expectancy = win_rate * average_win + (1.0 - win_rate) * average_loss

    return PerformanceReport(
        total_pnl=total_pnl,
        total_return=total_pnl / initial_balance if initial_balance > 0 else 0.0,
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        trade_count=int(pnls.size),
        win_count=int(wins.size),
        loss_count=int(losses.size),
        win_rate=win_rate,
        profit_factor=profit_factor,
        average_win=average_win,
        average_loss=average_loss,
        risk_reward_ratio=risk_reward,
        expectancy=expectancy,
        sharpe_ratio=sharpe_ratio(equity_curve),
        sortino_ratio=sortino_ratio(equity_curve),
        calmar_ratio=calmar_ratio(equity_curve),
        max_drawdown=max_drawdown(equity_curve),
        total_fees=float(sum(t.fees for t in trades)),
        equity_curve=equity_curve,
        drawdown_curve=drawdown_series(equity_curve),
    )
