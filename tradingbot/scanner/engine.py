"""Asynchrone Scan-Engine des Buy-the-Dip-Scanners.

Die :class:`ScannerEngine` läuft als eigenständige, permanente Loop –
vollständig getrennt vom Trading-Bot:

    1. Universum laden (eingebaute Indizes + eigene Ticker/CSV).
    2. Zyklus: Kursdaten in Batches laden (Thread-Pool, Cache, Retry),
       Benchmark aktualisieren, alle Symbole durch den Detektor schicken
       (chunk-weise in Threads, damit die Event-Loop reaktiv bleibt).
    3. Statusübergänge gegen den letzten Zyklus erkennen (neu, bestätigt,
       Einstieg, Ziel erreicht, ungültig) und Benachrichtigungen senden.
    4. Ergebnisse und Scan-Metadaten in der Datenbank persistieren –
       das Dashboard liest ausschließlich von dort.
"""

from __future__ import annotations

import asyncio
import time

import pandas as pd

from tradingbot.core.config import Config, ScannerConfig
from tradingbot.core.logging import get_logger
from tradingbot.database.repository import Database
from tradingbot.monitoring.notifier import (
    DiscordNotifier,
    EmailNotifier,
    Notifier,
    TelegramNotifier,
)
from tradingbot.scanner.data_provider import StockDataProvider, YFinanceProvider
from tradingbot.scanner.detector import DetectorConfig, DipDetector
from tradingbot.scanner.models import DipSignal, ScanCycleStats, SetupStatus
from tradingbot.scanner.paper_trader import ScannerPaperTrader
from tradingbot.scanner.universe import load_universe

logger = get_logger(__name__)

#: Chunk-Größe der Detektor-Ausführung im Thread-Pool.
_DETECT_CHUNK = 50

_CHANNEL_CLASSES: dict[str, type[Notifier]] = {
    "discord": DiscordNotifier,
    "telegram": TelegramNotifier,
    "email": EmailNotifier,
}


class ScannerNotifier:
    """Benachrichtigungen des Scanners (eigene Kanal-/Ereignis-Konfiguration)."""

    def __init__(self, config: ScannerConfig) -> None:
        self._config = config.notifications
        self._notifiers: list[Notifier] = []
        if self._config.enabled:
            for channel in self._config.channels:
                notifier = _CHANNEL_CLASSES[channel]()
                if notifier.is_configured():
                    self._notifiers.append(notifier)
                else:
                    logger.warning(
                        "Scanner-Kanal '%s' aktiviert, aber nicht konfiguriert", channel
                    )

    async def send(self, event: str, title: str, message: str) -> None:
        """Versendet ein Scanner-Ereignis über alle aktiven Kanäle.

        Args:
            event: Ereignisschlüssel (``new_setup``, ``confirmed``,
                ``entry_signal``, ``target_reached``, ``invalidated``).
            title: Nachrichtentitel.
            message: Nachrichtentext.
        """
        if not getattr(self._config.events, event, False):
            return
        for notifier in self._notifiers:
            try:
                await notifier.send(title, message)
            except Exception:
                logger.exception("Scanner-Benachrichtigung über '%s' fehlgeschlagen", notifier.name)


class ScannerEngine:
    """Permanenter Buy-the-Dip-Scanner.

    Args:
        config: Vollständige Bot-Konfiguration (verwendet ``config.scanner``
            und ``config.database``).
        provider: Optionaler Daten-Provider (Tests/DI); None = yfinance.
        database: Optionale Datenbank (Tests/DI); None = gemäß Konfiguration.
    """

    def __init__(
        self,
        config: Config,
        provider: StockDataProvider | None = None,
        database: Database | None = None,
    ) -> None:
        self._config = config.scanner
        self._db = database or Database(url=config.database.url, echo=False)
        self._provider = provider or YFinanceProvider(
            batch_size=self._config.batch_size,
            cache_ttl_seconds=self._config.cache_ttl_seconds,
            benchmark_symbol=self._config.benchmark_symbol,
        )
        self._detector = DipDetector(self._build_detector_config())
        self._notifier = ScannerNotifier(config.scanner)
        self._paper_trader = (
            ScannerPaperTrader(self._config.paper_trading, self._db)
            if self._config.paper_trading.enabled
            else None
        )
        self._universe: dict[str, str] = {}
        self._running = False
        self._last_stats: ScanCycleStats | None = None

    def _build_detector_config(self) -> DetectorConfig:
        d = self._config.detector
        return DetectorConfig(
            min_history=d.min_history,
            trend_lookback=d.trend_lookback,
            min_trend_gain=d.min_trend_gain,
            high_lookback=d.high_lookback,
            min_dip=d.min_dip,
            max_dip=d.max_dip,
            min_dip_bars=d.min_dip_bars,
            max_dip_bars=d.max_dip_bars,
            panic_atr_mult=d.panic_atr_mult,
            volume_spike_limit=d.volume_spike_limit,
            support_max_distance=d.support_max_distance,
            support_undercut_tolerance=d.support_undercut_tolerance,
            invalidation_pct=d.invalidation_pct,
            stop_atr_mult=d.stop_atr_mult,
            min_trend_score=d.min_trend_score,
            rs_lookback=d.rs_lookback,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @property
    def last_stats(self) -> ScanCycleStats | None:
        """Kennzahlen des letzten abgeschlossenen Zyklus."""
        return self._last_stats

    @property
    def paper_trader(self) -> ScannerPaperTrader | None:
        """Das Paper-Trading-Depot, falls aktiviert (sonst None)."""
        return self._paper_trader

    async def run_forever(self) -> None:
        """Startet die permanente Scan-Loop (bis zum Abbruch)."""
        self._running = True
        self._universe = load_universe(
            self._config.universes,
            custom_tickers=self._config.custom_tickers,
            csv_path=self._config.universe_csv,
        )
        logger.info(
            "Scanner gestartet: %d Symbole, Zyklus alle %.0fs, Mindest-Score %.0f",
            len(self._universe),
            self._config.interval_seconds,
            self._config.filters.min_score,
        )
        try:
            while self._running:
                try:
                    stats = await self.scan_once()
                    self._last_stats = stats
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Scan-Zyklus fehlgeschlagen – nächster Versuch nach Intervall")
                    self._db.log_error("Scan-Zyklus fehlgeschlagen", module="scanner")
                await asyncio.sleep(self._config.interval_seconds)
        except asyncio.CancelledError:
            pass
        finally:
            self._running = False
            try:
                status = self._db.get_scanner_status()
                if status is not None:
                    self._db.save_scanner_status(
                        universe_size=status.universe_size,
                        scanned_symbols=status.scanned_symbols,
                        signals_found=status.signals_found,
                        failed_symbols=status.failed_symbols,
                        duration_seconds=status.duration_seconds,
                        running=False,
                    )
            except Exception:
                logger.exception("Scanner-Status konnte beim Stopp nicht aktualisiert werden")
            logger.info("Scanner gestoppt")

    def stop(self) -> None:
        """Signalisiert der Loop, nach dem aktuellen Zyklus zu stoppen."""
        self._running = False

    # ------------------------------------------------------------------
    # Ein Scan-Zyklus
    # ------------------------------------------------------------------

    async def scan_once(self) -> ScanCycleStats:
        """Führt genau einen vollständigen Scan-Durchlauf aus.

        Returns:
            Kennzahlen des Durchlaufs.
        """
        started = time.monotonic()
        if not self._universe:
            self._universe = load_universe(
                self._config.universes,
                custom_tickers=self._config.custom_tickers,
                csv_path=self._config.universe_csv,
            )
        symbols = list(self._universe)

        benchmark: pd.DataFrame | None = None
        try:
            benchmark = await self._provider.fetch_benchmark(self._config.history_period)
        except Exception:
            logger.warning("Benchmark nicht verfügbar – relative Stärke = absolute Rendite")

        data = await self._provider.fetch_history(
            symbols, period=self._config.history_period, interval="1d"
        )
        failed = len(symbols) - len(data)

        # Detektion chunk-weise in Threads, damit die Loop nicht blockiert.
        signals: dict[str, DipSignal] = {}
        items = [(s, df) for s, df in data.items() if self._passes_prefilter(df)]
        for start in range(0, len(items), _DETECT_CHUNK):
            chunk = items[start : start + _DETECT_CHUNK]
            chunk_signals = await asyncio.to_thread(self._detect_chunk, chunk, benchmark)
            signals.update(chunk_signals)

        invalidated = await self._reconcile(signals)

        if self._paper_trader is not None:
            opened, closed = await asyncio.to_thread(
                self._paper_trader.process_cycle, signals, data, invalidated
            )
            for position in opened:
                await self._notifier.send(
                    "trade_opened",
                    f"💰 Paper-Kauf: {position.symbol}",
                    f"{position.name}\n{position.amount:.2f} Stk @ {position.entry_price:.2f}\n"
                    f"Stop: {position.stop_loss:.2f} | Ziel 1: {position.target_1:.2f} | "
                    f"Ziel 2: {position.target_2:.2f}",
                )
            for trade in closed:
                emoji = "✅" if trade.is_win else "❌"
                await self._notifier.send(
                    "trade_closed",
                    f"{emoji} Paper-Trade geschlossen: {trade.symbol}",
                    f"{trade.amount:.2f} Stk @ {trade.exit_price:.2f} ({trade.exit_reason})\n"
                    f"PnL: {trade.pnl:+.2f}",
                )

        duration = time.monotonic() - started
        stats = ScanCycleStats(
            scanned_symbols=len(data),
            signals_found=len(signals),
            failed_symbols=failed,
            duration_seconds=duration,
        )
        self._db.save_scanner_status(
            universe_size=len(symbols),
            scanned_symbols=stats.scanned_symbols,
            signals_found=stats.signals_found,
            failed_symbols=stats.failed_symbols,
            duration_seconds=duration,
            running=self._running,
        )
        logger.info(
            "Scan abgeschlossen: %d/%d Symbole analysiert, %d Setups (%.1fs)",
            stats.scanned_symbols,
            len(symbols),
            stats.signals_found,
            duration,
        )
        return stats

    def _passes_prefilter(self, df: pd.DataFrame) -> bool:
        """Preis- und Liquiditätsfilter vor der teureren Mustererkennung."""
        f = self._config.filters
        close = float(df["close"].iloc[-1])
        if close < f.min_price:
            return False
        if f.max_price > 0 and close > f.max_price:
            return False
        avg_volume = float(df["volume"].tail(20).mean())
        return avg_volume >= f.min_avg_volume

    def _detect_chunk(
        self,
        items: list[tuple[str, pd.DataFrame]],
        benchmark: pd.DataFrame | None,
    ) -> dict[str, DipSignal]:
        """Führt den Detektor über einen Symbol-Chunk aus (läuft im Thread)."""
        results: dict[str, DipSignal] = {}
        min_score = self._config.filters.min_score
        for symbol, df in items:
            try:
                signal = self._detector.detect(
                    symbol, self._universe.get(symbol, symbol), df, benchmark
                )
            except Exception:
                logger.exception("Detektor-Fehler bei %s – Symbol übersprungen", symbol)
                continue
            if signal is not None and signal.score >= min_score:
                results[symbol] = signal
        return results

    # ------------------------------------------------------------------
    # Statusübergänge & Persistenz
    # ------------------------------------------------------------------

    async def _reconcile(self, current: dict[str, DipSignal]) -> set[str]:
        """Vergleicht mit dem Vorzyklus, persistiert und benachrichtigt.

        Returns:
            Symbole, deren Setup in diesem Zyklus ungültig wurde (für den
            Paper-Trader – löst dort einen sofortigen Positions-Exit aus).
        """
        previous = {
            r.symbol: r for r in self._db.get_scanner_signals(active_only=True)
        }
        invalidated: set[str] = set()

        for symbol, signal in current.items():
            old = previous.get(symbol)

            # Ziel erreicht: vorher ENTRY, Kurs über Ziel 1 des gespeicherten Setups.
            if old is not None and old.status == SetupStatus.ENTRY.value and signal.price >= old.target_1 > 0:
                signal.status = SetupStatus.TARGET_REACHED
                self._db.upsert_scanner_signal(signal)
                await self._notifier.send(
                    "target_reached",
                    f"🎯 {symbol}: Kursziel 1 erreicht",
                    f"{signal.name}\nKurs: {signal.price:.2f}\nZiel 1: {old.target_1:.2f}\nScore: {signal.score:.0f}",
                )
                continue

            self._db.upsert_scanner_signal(signal)

            if old is None:
                await self._notifier.send(
                    "new_setup",
                    f"🔍 Neues Buy-the-Dip-Setup: {symbol}",
                    self._describe(signal),
                )
            else:
                old_status = old.status
                if old_status == SetupStatus.WATCHING.value and signal.status is SetupStatus.CONFIRMED:
                    await self._notifier.send(
                        "confirmed",
                        f"✅ Setup bestätigt: {symbol}",
                        self._describe(signal),
                    )
                elif signal.status is SetupStatus.ENTRY and old_status in (
                    SetupStatus.WATCHING.value,
                    SetupStatus.CONFIRMED.value,
                ):
                    await self._notifier.send(
                        "entry_signal",
                        f"🚀 Einstiegssignal: {symbol}",
                        self._describe(signal),
                    )

        # Verschwundene aktive Setups sind ungültig geworden.
        for symbol, old in previous.items():
            if symbol not in current:
                self._db.mark_scanner_signal_status(symbol, SetupStatus.INVALIDATED.value)
                invalidated.add(symbol)
                await self._notifier.send(
                    "invalidated",
                    f"❌ Setup ungültig: {symbol}",
                    f"{old.name}\nLetzter Score: {old.score:.0f}\n"
                    f"Unterstützung {old.support_level:.2f} gebrochen oder Trendkriterien verletzt.",
                )
        return invalidated

    @staticmethod
    def _describe(signal: DipSignal) -> str:
        """Kompakte Beschreibung eines Setups für Benachrichtigungen."""
        return (
            f"{signal.name}\n"
            f"Status: {signal.status.value} | Score: {signal.score:.0f}/100\n"
            f"Kurs: {signal.price:.2f} ({signal.change_pct:+.2%} heute)\n"
            f"Rücksetzer: {signal.drawdown_pct:.1%} vom Hoch {signal.recent_high:.2f}\n"
            f"Unterstützung: {signal.support_type.value} @ {signal.support_level:.2f} "
            f"({signal.support_distance_pct:+.1%})\n"
            f"Einstieg: {signal.entry_price:.2f} | Stop: {signal.stop_loss:.2f}\n"
            f"Ziel 1: {signal.target_1:.2f} | Ziel 2: {signal.target_2:.2f} | CRV: {signal.risk_reward:.1f}"
        )


async def run_scanner(config: Config) -> None:
    """Convenience-Einstiegspunkt: erstellt und startet die Scanner-Engine."""
    engine = ScannerEngine(config)
    await engine.run_forever()
