"""Haupt-Engine des Trading-Bots.

Die :class:`TradingEngine` verdrahtet alle Module (Exchange, Daten,
Strategien, Risiko, Execution, Persistenz, Benachrichtigungen) und
betreibt die asynchrone Haupt-Loop:

    1. Live-Kerzen treffen ein -> Strategien werden ausgewertet.
    2. Einstiegssignale durchlaufen Risiko-Prüfung und Positions-Sizing.
    3. Ticker-Updates treiben SL/TP/Trailing-Überwachung.
    4. Periodisch: Order-Sync, Equity-Snapshot, Risiko-Limits.

Der Bot startet standardmäßig im Paper-Modus; Live-Trading erfordert
die explizite Bestätigung in der Konfiguration (siehe
:class:`~tradingbot.core.config.TradingConfig`).
"""

from __future__ import annotations

import asyncio

import pandas as pd

from tradingbot.analytics.indicators import atr as atr_indicator
from tradingbot.core.config import Config
from tradingbot.core.enums import (
    SignalAction,
    SizingMethod,
    Timeframe,
    TradingMode,
)
from tradingbot.core.events import EventBus, EventType
from tradingbot.core.logging import get_logger
from tradingbot.core.models import Candle, Signal, Ticker
from tradingbot.data.stream import MarketDataStream
from tradingbot.database.repository import Database
from tradingbot.exchange.factory import create_exchange
from tradingbot.execution.engine import ExecutionEngine
from tradingbot.execution.order_manager import OrderManager
from tradingbot.monitoring.notifier import NotificationManager
from tradingbot.risk.manager import RiskManager
from tradingbot.risk.sizing import PositionSizer
from tradingbot.strategies.base import Strategy, StrategyContext
from tradingbot.strategies.registry import create_strategy

logger = get_logger(__name__)


class TradingEngine:
    """Orchestriert den kompletten Handelsbetrieb.

    Args:
        config: Validierte Bot-Konfiguration.
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        self._bus = EventBus()
        self._exchange = create_exchange(config)
        self._db = Database(url=config.database.url, echo=config.database.echo)
        self._stream = MarketDataStream(
            self._exchange, self._bus, history_size=config.trading.candle_history
        )
        self._order_manager = OrderManager(
            self._exchange, on_order_update=self._db.save_order
        )
        self._risk = RiskManager(config.risk, initial_equity=config.paper.initial_balance)
        self._sizer = PositionSizer(config.sizing, config.risk)
        self._execution = ExecutionEngine(
            self._order_manager, self._risk, self._bus, database=self._db
        )
        self._notifications = NotificationManager(config.notifications)
        self._strategies: list[Strategy] = self._build_strategies()
        self._quote_currency = self._detect_quote_currency()
        self._equity = config.paper.initial_balance
        self._running = False
        self._maintenance_task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------
    # Aufbau
    # ------------------------------------------------------------------

    def _build_strategies(self) -> list[Strategy]:
        """Instanziiert alle in der Konfiguration aktivierten Strategien."""
        strategies: list[Strategy] = []
        for entry in self._config.strategies.active:
            symbols = entry.symbols or self._config.trading.symbols
            strategy = create_strategy(
                entry.name, symbols=symbols, timeframe=entry.timeframe, params=entry.params
            )
            strategies.append(strategy)
            self._db.upsert_strategy(
                entry.name, strategy.params, symbols, entry.timeframe, enabled=True
            )
            logger.info("Strategie geladen: %r", strategy)
        if not strategies:
            logger.warning("Keine Strategien konfiguriert – der Bot beobachtet nur den Markt")
        return strategies

    def _detect_quote_currency(self) -> str:
        first = self._config.trading.symbols[0]
        return first.split("/")[1].split(":")[0] if "/" in first else "USDT"

    def _subscriptions(self) -> list[tuple[str, Timeframe]]:
        """Alle benötigten (Symbol, Timeframe)-Paare inkl. MTF-Zusatzframes."""
        subscriptions: set[tuple[str, Timeframe]] = set()
        for strategy in self._strategies:
            for symbol in strategy.symbols:
                subscriptions.add((symbol, strategy.timeframe))
                for extra_tf in strategy.additional_timeframes:
                    subscriptions.add((symbol, extra_tf))
        if not subscriptions:
            for symbol in self._config.trading.symbols:
                subscriptions.add((symbol, self._config.trading.timeframe))
        return sorted(subscriptions, key=lambda s: (s[0], s[1].seconds))

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Startet den Bot (verbindet, bootstrappt Daten, abonniert Events)."""
        mode = self._config.trading.mode
        logger.info(
            "TradingBot startet im %s-Modus auf %s (%s)",
            mode.value.upper(),
            self._config.trading.exchange,
            ", ".join(self._config.trading.symbols),
        )
        if mode is TradingMode.LIVE:
            logger.warning("LIVE-TRADING: Es wird echtes Kapital eingesetzt!")

        await self._exchange.connect()
        self._equity = await self._compute_equity()
        self._risk = RiskManager(self._config.risk, initial_equity=self._equity)
        # ExecutionEngine mit dem neuen RiskManager verbinden.
        self._execution = ExecutionEngine(
            self._order_manager, self._risk, self._bus, database=self._db
        )

        self._notifications.attach(self._bus)
        self._bus.subscribe(EventType.CANDLE, self._on_candle)
        self._bus.subscribe(EventType.TICKER, self._on_ticker)

        await self._stream.start(self._subscriptions())
        self._running = True
        self._maintenance_task = asyncio.create_task(self._maintenance_loop(), name="maintenance")
        logger.info(
            "Bot bereit: %d Strategien, Equity %.2f %s",
            len(self._strategies),
            self._equity,
            self._quote_currency,
        )

    async def stop(self) -> None:
        """Fährt den Bot geordnet herunter."""
        logger.info("TradingBot wird gestoppt ...")
        self._running = False
        if self._maintenance_task is not None:
            self._maintenance_task.cancel()
            try:
                await self._maintenance_task
            except asyncio.CancelledError:
                pass
        try:
            await self._order_manager.cancel_all()
        except Exception:
            logger.exception("Fehler beim Stornieren offener Orders")
        await self._stream.stop()
        await self._exchange.close()
        self._db.close()
        logger.info("TradingBot gestoppt")

    async def run_forever(self) -> None:
        """Startet den Bot und blockiert bis zum Abbruch (Strg+C)."""
        await self.start()
        try:
            while self._running:
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            pass
        finally:
            await self.stop()

    # ------------------------------------------------------------------
    # Event-Handler
    # ------------------------------------------------------------------

    async def _on_candle(self, candle: Candle) -> None:
        """Neue abgeschlossene Kerze: zuständige Strategien auswerten."""
        for strategy in self._strategies:
            if candle.symbol not in strategy.symbols or candle.timeframe is not strategy.timeframe:
                continue
            df = self._stream.get_candles(candle.symbol, strategy.timeframe)
            if df.empty:
                continue
            context = StrategyContext(
                position_side=self._execution.position_side,
                get_candles=self._stream.get_candles,
            )
            try:
                signal = strategy.generate_signal(df, candle.symbol, context)
            except Exception:
                logger.exception("Strategie %s warf eine Exception", strategy.name)
                self._db.log_error(
                    f"Strategie {strategy.name} fehlgeschlagen", module="engine"
                )
                await self._bus.publish(
                    EventType.ERROR, f"Strategie {strategy.name} warf eine Exception"
                )
                continue
            if signal is not None:
                await self._handle_signal(signal, df)

    async def _on_ticker(self, ticker: Ticker) -> None:
        """Ticker-Update: Exit-Überwachung der offenen Position."""
        trade = await self._execution.on_price_update(ticker.symbol, ticker.last)
        if trade is not None:
            await self._refresh_equity()

    # ------------------------------------------------------------------
    # Signal-Verarbeitung
    # ------------------------------------------------------------------

    async def _handle_signal(self, signal: Signal, df: pd.DataFrame) -> None:
        """Setzt ein Strategie-Signal um (Exit direkt, Entry via Risiko/Sizing)."""
        logger.info(
            "Signal: %s %s @ %.8f (%s: %s)",
            signal.action.value,
            signal.symbol,
            signal.price,
            signal.strategy,
            signal.reason,
        )
        await self._bus.publish(EventType.SIGNAL, signal)

        if signal.is_exit:
            trade = await self._execution.close_position(signal.symbol, reason="signal")
            if trade is not None:
                await self._execution.publish_trade(trade)
                await self._refresh_equity()
            return
        if not signal.is_entry:
            return

        decision = self._risk.evaluate_entry(signal, self._execution.open_position_count())
        if not decision.approved:
            logger.info("Einstieg abgelehnt (%s): %s", signal.symbol, decision.reason)
            return

        atr_value: float | None = None
        if self._config.sizing.method is SizingMethod.ATR and len(df) > self._config.sizing.atr_period:
            atr_series = atr_indicator(df, self._config.sizing.atr_period)
            if not atr_series.dropna().empty:
                atr_value = float(atr_series.iloc[-1])

        amount = self._sizer.compute(
            equity=self._equity,
            price=signal.price,
            stop_loss=decision.stop_loss,
            atr_value=atr_value,
            trade_history=self._execution.closed_trades,
            max_leverage=self._config.risk.max_leverage,
        )
        if amount <= 0:
            logger.info("Positionsgröße 0 für %s – Einstieg übersprungen", signal.symbol)
            return

        leverage = min(self._config.risk.max_leverage, self._config.backtest.leverage)
        position = await self._execution.execute_entry(
            signal, amount, decision, leverage=max(1.0, leverage)
        )
        if position is not None:
            await self._refresh_equity()

    # ------------------------------------------------------------------
    # Wartungs-Loop
    # ------------------------------------------------------------------

    async def _maintenance_loop(self) -> None:
        """Periodische Aufgaben: Order-Sync, Equity-Snapshots, Risiko-Limits."""
        interval = self._config.trading.loop_interval_seconds
        while self._running:
            try:
                await asyncio.sleep(interval)
                filled = await self._order_manager.sync_open_orders()
                for order in filled:
                    await self._bus.publish(EventType.ORDER_FILLED, order)
                await self._refresh_equity()
                self._db.save_performance_snapshot(
                    equity=self._equity,
                    balance=self._equity,
                    open_positions=self._execution.open_position_count(),
                    drawdown=self._risk.current_drawdown,
                    daily_pnl=-self._risk.daily_loss_pct * self._equity,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Fehler in der Wartungs-Loop")
                self._db.log_error("Wartungs-Loop-Fehler", module="engine")

    async def _refresh_equity(self) -> None:
        """Equity neu berechnen und Risiko-Limits prüfen."""
        self._equity = await self._compute_equity()
        halt_reason = self._risk.update_equity(self._equity)
        if halt_reason is not None:
            await self._bus.publish(EventType.RISK_LIMIT_HIT, halt_reason)

    async def _compute_equity(self) -> float:
        """Gesamtkapital in Quote-Währung (Guthaben + bewertete Bestände)."""
        try:
            balances = await self._exchange.fetch_balance()
        except Exception:
            logger.exception("Balance-Abruf fehlgeschlagen – letzte Equity wird beibehalten")
            return self._equity

        equity = 0.0
        for currency, balance in balances.items():
            if balance.total == 0:
                continue
            if currency == self._quote_currency:
                equity += balance.total
                continue
            price = self._stream.last_price(f"{currency}/{self._quote_currency}")
            if price is not None:
                equity += balance.total * price
        return equity if equity > 0 else self._equity

    # ------------------------------------------------------------------
    # Status (z. B. für Dashboard/CLI)
    # ------------------------------------------------------------------

    @property
    def status(self) -> dict[str, object]:
        """Kompakter Statusbericht des laufenden Bots."""
        halted, halt_reason = self._risk.is_halted
        return {
            "mode": self._config.trading.mode.value,
            "exchange": self._config.trading.exchange,
            "running": self._running,
            "equity": self._equity,
            "quote_currency": self._quote_currency,
            "open_positions": self._execution.open_position_count(),
            "closed_trades": len(self._execution.closed_trades),
            "drawdown": self._risk.current_drawdown,
            "halted": halted,
            "halt_reason": halt_reason,
            "strategies": [s.name for s in self._strategies],
        }
