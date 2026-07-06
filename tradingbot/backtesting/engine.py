"""Event-basierte Backtest-Engine.

Simuliert eine Strategie kerzenweise über historische Daten – für
mehrere Assets gleichzeitig – mit realistischen Kosten:

    * Kommission, Slippage und Spread pro Fill
    * Hebel (Margin-Modell auf Quote-Basis)
    * Stop-Loss / Take-Profit intra-bar (High/Low-Prüfung)
    * Trailing-Stop und Break-Even gemäß Risiko-Konfiguration
    * Positionsgrößen über den konfigurierten :class:`PositionSizer`
    * Tagesverlust-/Drawdown-Stopps auf Basis der Kerzen-Zeitstempel

Die Engine verwendet dieselben Strategie-Instanzen wie der Live-Betrieb;
Multi-Timeframe-Strategien erhalten resampelte Daten über den Kontext.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from tradingbot.analytics.performance import PerformanceReport, build_report
from tradingbot.core.config import RiskConfig, SizingConfig
from tradingbot.core.enums import PositionSide, SignalAction, Timeframe
from tradingbot.core.exceptions import BacktestError
from tradingbot.core.logging import get_logger
from tradingbot.core.models import Position, Signal, Trade
from tradingbot.data.resampler import resample_candles
from tradingbot.risk.sizing import PositionSizer
from tradingbot.strategies.base import Strategy, StrategyContext

logger = get_logger(__name__)


@dataclass(slots=True)
class BacktestSettings:
    """Kosten- und Kapitalparameter eines Backtests.

    Attributes:
        initial_balance: Startkapital in Quote-Währung.
        commission_rate: Kommission pro Fill als Bruchteil.
        slippage_rate: Slippage auf Marktausführungen als Bruchteil.
        spread_rate: Gesamter Spread als Bruchteil (halber Spread je Seite).
        leverage: Konstanter Hebel für alle Positionen.
        allow_short: Short-Positionen zulassen.
    """

    initial_balance: float = 10_000.0
    commission_rate: float = 0.001
    slippage_rate: float = 0.0005
    spread_rate: float = 0.0002
    leverage: float = 1.0
    allow_short: bool = True


@dataclass(slots=True)
class BacktestResult:
    """Ergebnis eines Backtest-Laufs.

    Attributes:
        strategy_name: Name der getesteten Strategie.
        params: Verwendete Strategie-Parameter.
        symbols: Getestete Symbole.
        timeframe: Haupt-Timeframe.
        report: Vollständiger Performance-Bericht.
        trades: Alle simulierten Trades.
        equity_curve: Equity-Verlauf (Mark-to-Market je Kerze).
        settings: Verwendete Kostenparameter.
    """

    strategy_name: str
    params: dict[str, Any]
    symbols: list[str]
    timeframe: Timeframe
    report: PerformanceReport
    trades: list[Trade] = field(default_factory=list)
    equity_curve: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    settings: BacktestSettings = field(default_factory=BacktestSettings)


class BacktestEngine:
    """Führt Backtests für eine Strategie über historische Daten aus.

    Args:
        settings: Kosten-/Kapitalparameter.
        risk_config: Risiko-Regeln (Stops, Limits, Cooldown-Konfiguration).
        sizing_config: Positionsgrößen-Konfiguration.
    """

    def __init__(
        self,
        settings: BacktestSettings | None = None,
        risk_config: RiskConfig | None = None,
        sizing_config: SizingConfig | None = None,
    ) -> None:
        self._settings = settings or BacktestSettings()
        self._risk = risk_config or RiskConfig()
        self._sizer = PositionSizer(sizing_config or SizingConfig(), self._risk)

    # ------------------------------------------------------------------
    # Öffentliche API
    # ------------------------------------------------------------------

    def run(
        self,
        strategy: Strategy,
        data: dict[str, pd.DataFrame],
        warmup: int | None = None,
    ) -> BacktestResult:
        """Führt den Backtest aus.

        Args:
            strategy: Konfigurierte Strategie-Instanz.
            data: OHLCV-DataFrames je Symbol im Haupt-Timeframe der Strategie.
            warmup: Kerzen vor dem ersten Signal (None = ``required_history``).

        Returns:
            :class:`BacktestResult` mit Trades, Equity-Kurve und Kennzahlen.

        Raises:
            BacktestError: Bei fehlenden oder unbrauchbaren Daten.
        """
        for symbol in strategy.symbols:
            if symbol not in data or data[symbol].empty:
                raise BacktestError(f"Keine Daten für Symbol '{symbol}' übergeben")

        warmup_bars = warmup if warmup is not None else strategy.required_history
        state = _SimulationState(self._settings.initial_balance)

        # Gemeinsame Zeitachse aller Symbole (Union, chronologisch).
        all_timestamps = sorted(
            set().union(*[set(df.index) for symbol, df in data.items() if symbol in strategy.symbols])
        )
        if len(all_timestamps) <= warmup_bars:
            raise BacktestError(
                f"Zu wenig Daten: {len(all_timestamps)} Kerzen bei {warmup_bars} Warmup-Kerzen"
            )

        # Vorbereitete Sichten für schnellen positionsbasierten Zugriff.
        positions_index: dict[str, pd.DatetimeIndex] = {
            symbol: df.index for symbol, df in data.items()
        }

        higher_tf_cache: dict[tuple[str, Timeframe], pd.DataFrame] = {}

        def get_candles(symbol: str, timeframe: Timeframe) -> pd.DataFrame:
            """Kontext-Callback: liefert (resampelte) Daten bis zur aktuellen Kerze."""
            df = data.get(symbol)
            if df is None:
                return pd.DataFrame()
            if timeframe is strategy.timeframe:
                return df.loc[: state.current_time]
            key = (symbol, timeframe)
            if key not in higher_tf_cache:
                higher_tf_cache[key] = resample_candles(df, strategy.timeframe, timeframe)
            resampled = higher_tf_cache[key]
            return resampled.loc[: state.current_time]

        context = StrategyContext(
            position_side=lambda symbol: (
                state.positions[symbol].side if symbol in state.positions else None
            ),
            get_candles=get_candles,
        )

        for i, timestamp in enumerate(all_timestamps):
            state.current_time = timestamp
            self._roll_day(state, timestamp.date())

            for symbol in strategy.symbols:
                df = data[symbol]
                if timestamp not in positions_index[symbol]:
                    continue
                bar = df.loc[timestamp]
                state.last_price[symbol] = float(bar["close"])

                # 1. Exits offener Positionen intra-bar prüfen (High/Low).
                if symbol in state.positions:
                    self._check_position_exit(state, symbol, bar, timestamp)

                # 2. Neue Signale erst nach der Warmup-Phase auswerten.
                #    (Bei Handelsstopp werden nur noch Exits verarbeitet.)
                if i < warmup_bars:
                    continue
                window = df.loc[:timestamp]
                try:
                    signal = strategy.generate_signal(window, symbol, context)
                except Exception:
                    logger.exception(
                        "Strategie %s warf Exception bei %s %s – Kerze übersprungen",
                        strategy.name, symbol, timestamp,
                    )
                    continue
                if signal is not None:
                    self._process_signal(state, signal, timestamp)

            # 3. Mark-to-Market-Equity je Zeitschritt festhalten.
            equity = self._mark_to_market(state)
            state.equity_points.append((timestamp, equity))
            self._check_halts(state, equity)

        # Offene Positionen zum letzten Kurs schließen.
        for symbol in list(state.positions):
            last_price = state.last_price.get(symbol)
            if last_price is not None:
                self._close_position(
                    state, symbol, last_price, all_timestamps[-1], reason="end_of_backtest"
                )

        equity_curve = pd.Series(
            [eq for _, eq in state.equity_points],
            index=pd.DatetimeIndex([ts for ts, _ in state.equity_points]),
            name="equity",
        )
        report = build_report(state.trades, equity_curve, self._settings.initial_balance)
        logger.info(
            "Backtest %s: %d Trades, PnL %.2f (%.2f%%), Sharpe %.2f, MaxDD %.1f%%",
            strategy.name,
            report.trade_count,
            report.total_pnl,
            report.total_return * 100,
            report.sharpe_ratio,
            report.max_drawdown * 100,
        )
        return BacktestResult(
            strategy_name=strategy.name,
            params=dict(strategy.params),
            symbols=list(strategy.symbols),
            timeframe=strategy.timeframe,
            report=report,
            trades=state.trades,
            equity_curve=equity_curve,
            settings=self._settings,
        )

    # ------------------------------------------------------------------
    # Signal-Verarbeitung
    # ------------------------------------------------------------------

    def _process_signal(self, state: "_SimulationState", signal: Signal, timestamp: pd.Timestamp) -> None:
        """Setzt ein Strategie-Signal in der Simulation um."""
        symbol = signal.symbol
        position = state.positions.get(symbol)

        if signal.action in (SignalAction.CLOSE_LONG, SignalAction.CLOSE_SHORT):
            if position is not None:
                expected = (
                    PositionSide.LONG
                    if signal.action is SignalAction.CLOSE_LONG
                    else PositionSide.SHORT
                )
                if position.side is expected:
                    self._close_position(state, symbol, signal.price, timestamp, reason="signal")
            return

        if not signal.is_entry or position is not None:
            return
        if state.halted or state.daily_halted:
            return
        if signal.action is SignalAction.SELL and not self._settings.allow_short:
            return
        if len(state.positions) >= self._risk.max_open_positions:
            return
        if state.daily_trades >= self._risk.max_daily_trades:
            return

        equity = self._mark_to_market(state)
        stop_loss, take_profit = self._effective_stops(signal)
        try:
            amount = self._sizer.compute(
                equity=equity,
                price=signal.price,
                stop_loss=stop_loss,
                trade_history=state.trades,
                max_leverage=self._settings.leverage,
            )
        except Exception:
            logger.exception("Sizing fehlgeschlagen für %s", symbol)
            return
        if amount <= 0:
            return

        is_long = signal.action is SignalAction.BUY
        fill_price = self._fill_price(signal.price, is_buy=is_long)
        notional = amount * fill_price
        margin = notional / self._settings.leverage
        fee = notional * self._settings.commission_rate

        if margin + fee > state.cash:
            # Auf verfügbares Kapital herunterskalieren.
            scale = state.cash / (margin + fee)
            amount *= scale * 0.999
            if amount <= 0:
                return
            notional = amount * fill_price
            margin = notional / self._settings.leverage
            fee = notional * self._settings.commission_rate

        state.cash -= margin + fee
        state.positions[symbol] = Position(
            symbol=symbol,
            side=PositionSide.LONG if is_long else PositionSide.SHORT,
            amount=amount,
            entry_price=fill_price,
            leverage=self._settings.leverage,
            stop_loss=stop_loss,
            take_profit=take_profit,
            trailing_stop=self._risk.trailing_stop if self._risk.trailing_stop > 0 else None,
            strategy=signal.strategy,
            opened_at=timestamp.to_pydatetime(),
            fees_paid=fee,
        )

    def _effective_stops(self, signal: Signal) -> tuple[float | None, float | None]:
        """Strategie-Stops mit Config-Defaults kombinieren (wie im Live-Betrieb)."""
        price = signal.price
        is_long = signal.action is SignalAction.BUY
        stop_loss = signal.stop_loss
        if stop_loss is None and self._risk.stop_loss > 0:
            stop_loss = price * (1 - self._risk.stop_loss) if is_long else price * (1 + self._risk.stop_loss)
        take_profit = signal.take_profit
        if take_profit is None and self._risk.take_profit > 0:
            take_profit = (
                price * (1 + self._risk.take_profit) if is_long else price * (1 - self._risk.take_profit)
            )
        return stop_loss, take_profit

    # ------------------------------------------------------------------
    # Positions-Überwachung
    # ------------------------------------------------------------------

    def _check_position_exit(
        self, state: "_SimulationState", symbol: str, bar: pd.Series, timestamp: pd.Timestamp
    ) -> None:
        """Prüft SL/TP/Trailing/Break-Even einer Position intra-bar."""
        position = state.positions[symbol]
        high, low, close = float(bar["high"]), float(bar["low"]), float(bar["close"])

        # Extremwerte und dynamische Stops aktualisieren.
        position.update_extremes(high)
        position.update_extremes(low)
        self._apply_break_even(position, high if position.side is PositionSide.LONG else low)
        self._apply_trailing(position)

        exit_price: float | None = None
        reason = ""
        if position.side is PositionSide.LONG:
            if position.stop_loss is not None and low <= position.stop_loss:
                exit_price, reason = min(position.stop_loss, high), "stop_loss"
            elif position.take_profit is not None and high >= position.take_profit:
                exit_price, reason = position.take_profit, "take_profit"
        else:
            if position.stop_loss is not None and high >= position.stop_loss:
                exit_price, reason = max(position.stop_loss, low), "stop_loss"
            elif position.take_profit is not None and low <= position.take_profit:
                exit_price, reason = position.take_profit, "take_profit"

        if exit_price is not None:
            self._close_position(state, symbol, exit_price, timestamp, reason=reason)
        else:
            state.last_price[symbol] = close

    def _apply_break_even(self, position: Position, price: float) -> None:
        trigger = self._risk.break_even_trigger
        if trigger <= 0 or position.break_even_done:
            return
        if position.side is PositionSide.LONG and price >= position.entry_price * (1 + trigger):
            if position.stop_loss is None or position.entry_price > position.stop_loss:
                position.stop_loss = position.entry_price
            position.break_even_done = True
        elif position.side is PositionSide.SHORT and price <= position.entry_price * (1 - trigger):
            if position.stop_loss is None or position.entry_price < position.stop_loss:
                position.stop_loss = position.entry_price
            position.break_even_done = True

    def _apply_trailing(self, position: Position) -> None:
        delta = position.trailing_stop
        if delta is None or delta <= 0:
            return
        if position.side is PositionSide.LONG:
            candidate = position.highest_price * (1 - delta)
            if position.stop_loss is None or candidate > position.stop_loss:
                position.stop_loss = candidate
        else:
            candidate = position.lowest_price * (1 + delta)
            if position.stop_loss is None or candidate < position.stop_loss:
                position.stop_loss = candidate

    def _close_position(
        self,
        state: "_SimulationState",
        symbol: str,
        price: float,
        timestamp: pd.Timestamp,
        reason: str,
    ) -> None:
        """Schließt eine Position und verbucht den Trade."""
        position = state.positions.pop(symbol)
        is_long = position.side is PositionSide.LONG
        fill_price = self._fill_price(price, is_buy=not is_long)
        notional = position.amount * fill_price
        fee = notional * self._settings.commission_rate

        if is_long:
            gross = (fill_price - position.entry_price) * position.amount
        else:
            gross = (position.entry_price - fill_price) * position.amount
        net = gross - fee - position.fees_paid

        margin = position.amount * position.entry_price / position.leverage
        state.cash += margin + gross - fee

        trade = Trade(
            symbol=symbol,
            side=position.side,
            amount=position.amount,
            entry_price=position.entry_price,
            exit_price=fill_price,
            pnl=net,
            fees=fee + position.fees_paid,
            strategy=position.strategy,
            opened_at=position.opened_at,
            closed_at=timestamp.to_pydatetime(),
            exit_reason=reason,
            leverage=position.leverage,
        )
        state.trades.append(trade)
        state.daily_trades += 1
        state.daily_pnl += net

    # ------------------------------------------------------------------
    # Bewertung & Limits
    # ------------------------------------------------------------------

    def _fill_price(self, price: float, is_buy: bool) -> float:
        """Ausführungspreis inkl. halbem Spread und Slippage."""
        adjustment = self._settings.spread_rate / 2.0 + self._settings.slippage_rate
        return price * (1.0 + adjustment) if is_buy else price * (1.0 - adjustment)

    def _mark_to_market(self, state: "_SimulationState") -> float:
        """Gesamtkapital = Cash + Margin + unrealisierter PnL aller Positionen."""
        equity = state.cash
        for symbol, position in state.positions.items():
            price = state.last_price.get(symbol, position.entry_price)
            margin = position.amount * position.entry_price / position.leverage
            equity += margin + position.unrealized_pnl(price)
        return equity

    def _check_halts(self, state: "_SimulationState", equity: float) -> None:
        """Drawdown- und Tagesverlust-Stopp analog zum Live-RiskManager."""
        state.peak_equity = max(state.peak_equity, equity)
        drawdown = 1.0 - equity / state.peak_equity if state.peak_equity > 0 else 0.0
        if drawdown >= self._risk.max_drawdown:
            if not state.halted:
                logger.warning("Backtest-Handelsstopp: Max Drawdown %.1f%% erreicht", drawdown * 100)
            state.halted = True
        if state.day_start_equity > 0:
            daily_loss = -state.daily_pnl / state.day_start_equity
            if daily_loss >= self._risk.max_daily_loss:
                state.daily_halted = True

    def _roll_day(self, state: "_SimulationState", day: date) -> None:
        """Setzt Tageszähler bei Datumswechsel zurück."""
        if state.current_day != day:
            state.current_day = day
            state.daily_trades = 0
            state.daily_pnl = 0.0
            state.daily_halted = False
            state.day_start_equity = self._mark_to_market(state)


@dataclass(slots=True)
class _SimulationState:
    """Interner, veränderlicher Zustand eines Backtest-Laufs."""

    cash: float
    positions: dict[str, Position] = field(default_factory=dict)
    trades: list[Trade] = field(default_factory=list)
    last_price: dict[str, float] = field(default_factory=dict)
    equity_points: list[tuple[pd.Timestamp, float]] = field(default_factory=list)
    peak_equity: float = 0.0
    halted: bool = False
    daily_halted: bool = False
    current_day: date | None = None
    daily_trades: int = 0
    daily_pnl: float = 0.0
    day_start_equity: float = 0.0
    current_time: pd.Timestamp | None = None

    def __post_init__(self) -> None:
        self.peak_equity = self.cash
        self.day_start_equity = self.cash
