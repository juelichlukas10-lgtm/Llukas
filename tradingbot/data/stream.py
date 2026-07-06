"""Live-Marktdaten-Stream-Manager.

Der :class:`MarketDataStream` hält rollierende Kerzen-Puffer je
(Symbol, Timeframe), speist sie initial per REST-Bootstrap und hält sie
anschließend über WebSocket-Streams aktuell. Neue Kerzen und Ticker
werden auf dem Event-Bus publiziert. Die Wiederverbindung übernimmt der
Exchange-Adapter; der Stream-Manager startet abgestürzte Tasks neu.
"""

from __future__ import annotations

import asyncio
from collections import deque

import pandas as pd

from tradingbot.core.enums import Timeframe
from tradingbot.core.events import EventBus, EventType
from tradingbot.core.logging import get_logger
from tradingbot.core.models import Candle, Ticker
from tradingbot.data.storage import candles_to_dataframe
from tradingbot.exchange.base import ExchangeAdapter, retry_async

logger = get_logger(__name__)

#: Wartezeit, bevor ein abgestürzter Stream-Task neu gestartet wird (Sekunden).
_TASK_RESTART_DELAY = 5.0


class MarketDataStream:
    """Verwaltet Live-Kerzen und Ticker für mehrere Symbole/Timeframes.

    Args:
        exchange: Verbundener Exchange-Adapter.
        event_bus: Bus, auf dem Candle-/Ticker-Ereignisse publiziert werden.
        history_size: Maximale Puffergröße je (Symbol, Timeframe).
    """

    def __init__(
        self,
        exchange: ExchangeAdapter,
        event_bus: EventBus,
        history_size: int = 500,
    ) -> None:
        self._exchange = exchange
        self._bus = event_bus
        self._history_size = history_size
        self._buffers: dict[tuple[str, Timeframe], deque[Candle]] = {}
        self._tickers: dict[str, Ticker] = {}
        self._tasks: list[asyncio.Task[None]] = []
        self._running = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self, subscriptions: list[tuple[str, Timeframe]]) -> None:
        """Bootstrappt Historien und startet die Live-Streams.

        Args:
            subscriptions: Liste von (Symbol, Timeframe)-Paaren.
        """
        self._running = True
        unique_symbols = sorted({symbol for symbol, _ in subscriptions})

        for symbol, timeframe in subscriptions:
            await self._bootstrap(symbol, timeframe)
            self._tasks.append(
                asyncio.create_task(
                    self._run_candle_stream(symbol, timeframe),
                    name=f"candles:{symbol}:{timeframe.value}",
                )
            )
        for symbol in unique_symbols:
            self._tasks.append(
                asyncio.create_task(self._run_ticker_stream(symbol), name=f"ticker:{symbol}")
            )
        logger.info(
            "MarketDataStream gestartet: %d Kerzen-Streams, %d Ticker-Streams",
            len(subscriptions),
            len(unique_symbols),
        )

    async def stop(self) -> None:
        """Stoppt alle Stream-Tasks."""
        self._running = False
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        logger.info("MarketDataStream gestoppt")

    # ------------------------------------------------------------------
    # Datenzugriff
    # ------------------------------------------------------------------

    def get_candles(self, symbol: str, timeframe: Timeframe) -> pd.DataFrame:
        """Aktueller Kerzen-Puffer als OHLCV-DataFrame (kann leer sein)."""
        buffer = self._buffers.get((symbol, timeframe))
        if not buffer:
            return candles_to_dataframe([])
        return candles_to_dataframe(list(buffer))

    def get_ticker(self, symbol: str) -> Ticker | None:
        """Jüngster Ticker eines Symbols oder None."""
        return self._tickers.get(symbol)

    def last_price(self, symbol: str) -> float | None:
        """Letzter bekannter Preis (Ticker bevorzugt, sonst letzte Kerze)."""
        ticker = self._tickers.get(symbol)
        if ticker is not None:
            return ticker.last
        for (sym, _), buffer in self._buffers.items():
            if sym == symbol and buffer:
                return buffer[-1].close
        return None

    # ------------------------------------------------------------------
    # Interne Stream-Logik
    # ------------------------------------------------------------------

    async def _bootstrap(self, symbol: str, timeframe: Timeframe) -> None:
        """Füllt den Puffer initial über die REST-API."""
        candles = await retry_async(
            lambda: self._exchange.fetch_ohlcv(symbol, timeframe, limit=self._history_size),
            description=f"bootstrap {symbol} {timeframe.value}",
        )
        buffer: deque[Candle] = deque(maxlen=self._history_size)
        buffer.extend(candles)
        self._buffers[(symbol, timeframe)] = buffer
        logger.info("Bootstrap %s %s: %d Kerzen geladen", symbol, timeframe.value, len(candles))

    def _update_buffer(self, candle: Candle) -> bool:
        """Fügt eine Kerze in den Puffer ein.

        Eine noch offene Kerze (gleicher Timestamp wie die letzte) wird
        ersetzt; eine neue Kerze wird angehängt.

        Returns:
            True, wenn eine *neue* Kerze begonnen hat (vorherige ist final).
        """
        key = (candle.symbol, candle.timeframe)
        buffer = self._buffers.setdefault(key, deque(maxlen=self._history_size))
        if buffer and buffer[-1].timestamp == candle.timestamp:
            buffer[-1] = candle
            return False
        if buffer and candle.timestamp < buffer[-1].timestamp:
            return False  # veraltete Kerze ignorieren
        buffer.append(candle)
        return len(buffer) > 1

    async def _run_candle_stream(self, symbol: str, timeframe: Timeframe) -> None:
        """Konsumiert den Kerzen-Stream; startet sich bei Fehlern neu."""
        while self._running:
            try:
                async for candle in self._exchange.watch_candles(symbol, timeframe):
                    new_candle_started = self._update_buffer(candle)
                    if new_candle_started:
                        buffer = self._buffers[(symbol, timeframe)]
                        closed = buffer[-2]
                        await self._bus.publish(EventType.CANDLE, closed)
                if not self._running:
                    return
                # Stream endete regulär (z. B. Mock) – nicht neu starten.
                return
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception(
                    "Kerzen-Stream %s %s abgestürzt – Neustart in %.0fs",
                    symbol,
                    timeframe.value,
                    _TASK_RESTART_DELAY,
                )
                await asyncio.sleep(_TASK_RESTART_DELAY)

    async def _run_ticker_stream(self, symbol: str) -> None:
        """Konsumiert den Ticker-Stream; startet sich bei Fehlern neu."""
        while self._running:
            try:
                async for ticker in self._exchange.watch_ticker(symbol):
                    self._tickers[symbol] = ticker
                    await self._bus.publish(EventType.TICKER, ticker)
                if not self._running:
                    return
                return
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception(
                    "Ticker-Stream %s abgestürzt – Neustart in %.0fs", symbol, _TASK_RESTART_DELAY
                )
                await asyncio.sleep(_TASK_RESTART_DELAY)
