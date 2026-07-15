"""Repository-Fassade über der SQLAlchemy-Persistenz.

Die Klasse :class:`Database` kapselt Engine, Session-Handling und alle
CRUD-Operationen. Unterstützt SQLite (Standard) und PostgreSQL über die
konfigurierte SQLAlchemy-URL.
"""

from __future__ import annotations

import traceback as tb_module
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator

if TYPE_CHECKING:
    from tradingbot.scanner.models import DipSignal

from sqlalchemy import create_engine, delete, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from tradingbot.core.enums import Timeframe
from tradingbot.core.exceptions import DatabaseError
from tradingbot.core.logging import get_logger
from tradingbot.core.models import Order, Position, Trade, new_id
from tradingbot.database.models import (
    BacktestRecord,
    Base,
    ErrorLogRecord,
    OrderRecord,
    PerformanceRecord,
    PositionRecord,
    ScannerSignalRecord,
    ScannerStatusRecord,
    StrategyRecord,
    TradeRecord,
)

logger = get_logger(__name__)


class Database:
    """Zentrale Persistenz-Fassade.

    Args:
        url: SQLAlchemy-Datenbank-URL (``sqlite:///...`` oder
            ``postgresql+psycopg2://...``).
        echo: SQL-Statements loggen (Debugging).
    """

    def __init__(
        self,
        url: str = "sqlite:///storage/tradingbot.db",
        echo: bool = False,
        connect_retries: int = 3,
    ) -> None:
        if url.startswith("sqlite:///"):
            db_path = Path(url.removeprefix("sqlite:///"))
            if db_path.parent != Path("."):
                db_path.parent.mkdir(parents=True, exist_ok=True)

        # pool_pre_ping: prueft jede Verbindung vor Gebrauch und ersetzt tote
        # Verbindungen transparent (schuetzt vor Servern, die Verbindungen nach
        # Inaktivitaet kappen, z. B. Neon-Autosuspend).
        # pool_recycle: verwirft Verbindungen vorsorglich nach 280s, bevor
        # typische Idle-Timeouts serverlos gehosteter Postgres-Instanzen greifen.
        engine_kwargs: dict[str, Any] = {"echo": echo, "future": True, "pool_pre_ping": True}
        if not url.startswith("sqlite"):
            engine_kwargs["pool_recycle"] = 280

        last_error: Exception | None = None
        for attempt in range(1, connect_retries + 1):
            try:
                self._engine: Engine = create_engine(url, **engine_kwargs)
                Base.metadata.create_all(self._engine)
                last_error = None
                break
            except Exception as exc:
                last_error = exc
                self._engine = None  # type: ignore[assignment]
                if attempt < connect_retries:
                    delay = 2.0 * attempt
                    logger.warning(
                        "Datenbankverbindung fehlgeschlagen (Versuch %d/%d): %s – "
                        "neuer Versuch in %.0fs (z. B. serverlose DB wacht gerade auf)",
                        attempt, connect_retries, exc, delay,
                    )
                    time.sleep(delay)
        if last_error is not None:
            raise DatabaseError(
                f"Datenbank-Initialisierung fehlgeschlagen nach {connect_retries} Versuchen "
                f"({url}): {last_error}"
            ) from last_error

        self._session_factory = sessionmaker(bind=self._engine, expire_on_commit=False)
        logger.info("Datenbank verbunden: %s", url.split("@")[-1])

    @contextmanager
    def session(self) -> Iterator[Session]:
        """Kontextmanager mit Commit/Rollback-Semantik.

        Yields:
            Offene SQLAlchemy-Session.

        Raises:
            DatabaseError: Bei Fehlern während der Transaktion.
        """
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception as exc:
            session.rollback()
            raise DatabaseError(f"Datenbank-Transaktion fehlgeschlagen: {exc}") from exc
        finally:
            session.close()

    def close(self) -> None:
        """Schließt den Verbindungspool."""
        self._engine.dispose()

    # ------------------------------------------------------------------
    # Trades
    # ------------------------------------------------------------------

    def save_trade(self, trade: Trade) -> None:
        """Persistiert einen abgeschlossenen Trade."""
        with self.session() as session:
            session.merge(
                TradeRecord(
                    id=trade.id,
                    symbol=trade.symbol,
                    side=trade.side.value,
                    amount=trade.amount,
                    entry_price=trade.entry_price,
                    exit_price=trade.exit_price,
                    pnl=trade.pnl,
                    fees=trade.fees,
                    leverage=trade.leverage,
                    strategy=trade.strategy,
                    exit_reason=trade.exit_reason,
                    opened_at=trade.opened_at,
                    closed_at=trade.closed_at,
                )
            )

    def get_trades(
        self,
        symbol: str | None = None,
        strategy: str | None = None,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> list[TradeRecord]:
        """Lädt Trades, optional gefiltert, absteigend nach Schlusszeit."""
        with self.session() as session:
            stmt = select(TradeRecord).order_by(TradeRecord.closed_at.desc())
            if symbol is not None:
                stmt = stmt.where(TradeRecord.symbol == symbol)
            if strategy is not None:
                stmt = stmt.where(TradeRecord.strategy == strategy)
            if since is not None:
                stmt = stmt.where(TradeRecord.closed_at >= since)
            if limit is not None:
                stmt = stmt.limit(limit)
            return list(session.scalars(stmt).all())

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------

    def save_order(self, order: Order) -> None:
        """Persistiert eine Order (Insert oder Update)."""
        with self.session() as session:
            session.merge(
                OrderRecord(
                    id=order.id,
                    exchange_id=order.exchange_id,
                    symbol=order.symbol,
                    side=order.side.value,
                    type=order.type.value,
                    amount=order.amount,
                    price=order.price,
                    stop_price=order.stop_price,
                    status=order.status.value,
                    filled=order.filled,
                    average_price=order.average_price,
                    fee=order.fee,
                    strategy=order.strategy,
                    created_at=order.created_at,
                    updated_at=order.updated_at,
                )
            )

    def get_orders(
        self, symbol: str | None = None, limit: int | None = 100
    ) -> list[OrderRecord]:
        """Lädt Orders, absteigend nach Erstellungszeit."""
        with self.session() as session:
            stmt = select(OrderRecord).order_by(OrderRecord.created_at.desc())
            if symbol is not None:
                stmt = stmt.where(OrderRecord.symbol == symbol)
            if limit is not None:
                stmt = stmt.limit(limit)
            return list(session.scalars(stmt).all())

    # ------------------------------------------------------------------
    # Positionen
    # ------------------------------------------------------------------

    def save_position(self, position: Position) -> None:
        """Persistiert den Snapshot einer offenen Position."""
        with self.session() as session:
            session.merge(
                PositionRecord(
                    id=position.id,
                    symbol=position.symbol,
                    side=position.side.value,
                    amount=position.amount,
                    entry_price=position.entry_price,
                    leverage=position.leverage,
                    stop_loss=position.stop_loss,
                    take_profit=position.take_profit,
                    strategy=position.strategy,
                    opened_at=position.opened_at,
                )
            )

    def delete_position(self, position_id: str) -> None:
        """Entfernt eine (geschlossene) Position."""
        with self.session() as session:
            session.execute(delete(PositionRecord).where(PositionRecord.id == position_id))

    def get_positions(self) -> list[PositionRecord]:
        """Alle offenen Positionen."""
        with self.session() as session:
            return list(session.scalars(select(PositionRecord)).all())

    # ------------------------------------------------------------------
    # Performance
    # ------------------------------------------------------------------

    def save_performance_snapshot(
        self,
        equity: float,
        balance: float,
        open_positions: int = 0,
        daily_pnl: float = 0.0,
        drawdown: float = 0.0,
        timestamp: datetime | None = None,
    ) -> None:
        """Speichert einen Equity-Snapshot."""
        with self.session() as session:
            session.add(
                PerformanceRecord(
                    timestamp=timestamp or datetime.now(timezone.utc),
                    equity=equity,
                    balance=balance,
                    open_positions=open_positions,
                    daily_pnl=daily_pnl,
                    drawdown=drawdown,
                )
            )

    def get_performance_history(
        self, since: datetime | None = None, limit: int | None = None
    ) -> list[PerformanceRecord]:
        """Equity-Historie, aufsteigend nach Zeit."""
        with self.session() as session:
            stmt = select(PerformanceRecord).order_by(PerformanceRecord.timestamp.asc())
            if since is not None:
                stmt = stmt.where(PerformanceRecord.timestamp >= since)
            if limit is not None:
                stmt = stmt.limit(limit)
            return list(session.scalars(stmt).all())

    # ------------------------------------------------------------------
    # Strategien
    # ------------------------------------------------------------------

    def upsert_strategy(
        self,
        name: str,
        params: dict[str, Any],
        symbols: list[str],
        timeframe: Timeframe,
        enabled: bool = True,
    ) -> None:
        """Legt eine Strategie an oder aktualisiert ihre Parameter."""
        with self.session() as session:
            existing = session.scalar(select(StrategyRecord).where(StrategyRecord.name == name))
            if existing is None:
                session.add(
                    StrategyRecord(
                        name=name,
                        params=params,
                        symbols=symbols,
                        timeframe=timeframe.value,
                        enabled=1 if enabled else 0,
                        updated_at=datetime.now(timezone.utc),
                    )
                )
            else:
                existing.params = params
                existing.symbols = symbols
                existing.timeframe = timeframe.value
                existing.enabled = 1 if enabled else 0
                existing.updated_at = datetime.now(timezone.utc)

    def get_strategies(self) -> list[StrategyRecord]:
        """Alle gespeicherten Strategien."""
        with self.session() as session:
            return list(session.scalars(select(StrategyRecord)).all())

    # ------------------------------------------------------------------
    # Fehlerlogs
    # ------------------------------------------------------------------

    def log_error(
        self, message: str, module: str = "", level: str = "ERROR", exc: BaseException | None = None
    ) -> None:
        """Persistiert einen Fehler (schluckt eigene DB-Fehler bewusst)."""
        try:
            with self.session() as session:
                session.add(
                    ErrorLogRecord(
                        timestamp=datetime.now(timezone.utc),
                        level=level,
                        module=module,
                        message=message,
                        traceback="".join(tb_module.format_exception(exc)) if exc else "",
                    )
                )
        except DatabaseError:
            logger.exception("Fehlerlog konnte nicht gespeichert werden")

    def get_error_logs(self, limit: int = 100) -> list[ErrorLogRecord]:
        """Jüngste Fehlerlogs, absteigend nach Zeit."""
        with self.session() as session:
            stmt = (
                select(ErrorLogRecord)
                .order_by(ErrorLogRecord.timestamp.desc())
                .limit(limit)
            )
            return list(session.scalars(stmt).all())

    # ------------------------------------------------------------------
    # Backtests
    # ------------------------------------------------------------------

    def save_backtest(
        self,
        strategy: str,
        symbols: list[str],
        timeframe: Timeframe,
        params: dict[str, Any],
        start: datetime,
        end: datetime,
        initial_balance: float,
        final_equity: float,
        metrics: dict[str, Any],
        backtest_id: str | None = None,
    ) -> str:
        """Speichert ein Backtest-Ergebnis.

        Returns:
            Die ID des gespeicherten Backtests.
        """
        record_id = backtest_id or new_id()
        with self.session() as session:
            session.merge(
                BacktestRecord(
                    id=record_id,
                    strategy=strategy,
                    symbols=symbols,
                    timeframe=timeframe.value,
                    params=params,
                    start=start,
                    end=end,
                    initial_balance=initial_balance,
                    final_equity=final_equity,
                    metrics=metrics,
                    created_at=datetime.now(timezone.utc),
                )
            )
        return record_id

    def get_backtests(self, strategy: str | None = None, limit: int = 50) -> list[BacktestRecord]:
        """Gespeicherte Backtests, absteigend nach Erstellungszeit."""
        with self.session() as session:
            stmt = select(BacktestRecord).order_by(BacktestRecord.created_at.desc()).limit(limit)
            if strategy is not None:
                stmt = stmt.where(BacktestRecord.strategy == strategy)
            return list(session.scalars(stmt).all())

    # ------------------------------------------------------------------
    # Marktscanner
    # ------------------------------------------------------------------

    def upsert_scanner_signal(self, signal: "DipSignal") -> None:  # noqa: F821
        """Speichert/aktualisiert ein Scanner-Signal (Schlüssel: Symbol).

        Der Ersterkennungs-Zeitpunkt (``detected_at``) eines bereits
        vorhandenen Signals bleibt erhalten.
        """
        with self.session() as session:
            existing = session.get(ScannerSignalRecord, signal.symbol)
            detected_at = existing.detected_at if existing is not None else signal.detected_at
            session.merge(
                ScannerSignalRecord(
                    symbol=signal.symbol,
                    name=signal.name,
                    status=signal.status.value,
                    score=signal.score,
                    price=signal.price,
                    change_pct=signal.change_pct,
                    recent_high=signal.recent_high,
                    drawdown_pct=signal.drawdown_pct,
                    support_type=signal.support_type.value,
                    support_level=signal.support_level,
                    support_distance_pct=signal.support_distance_pct,
                    trend_strength=signal.trend_strength,
                    rsi=signal.rsi,
                    volume=signal.volume,
                    volume_ratio=signal.volume_ratio,
                    relative_strength=signal.relative_strength,
                    atr=signal.atr,
                    entry_price=signal.entry_price,
                    stop_loss=signal.stop_loss,
                    target_1=signal.target_1,
                    target_2=signal.target_2,
                    risk_reward=signal.risk_reward,
                    score_breakdown=signal.score_breakdown,
                    detected_at=detected_at,
                    updated_at=signal.updated_at,
                )
            )

    def mark_scanner_signal_status(self, symbol: str, status: str) -> None:
        """Setzt nur den Status eines Signals (z. B. ``invalidated``)."""
        with self.session() as session:
            record = session.get(ScannerSignalRecord, symbol)
            if record is not None:
                record.status = status
                record.updated_at = datetime.now(timezone.utc)

    def get_scanner_signals(
        self,
        active_only: bool = True,
        min_score: float = 0.0,
        limit: int | None = None,
    ) -> list[ScannerSignalRecord]:
        """Scanner-Signale, absteigend nach Score sortiert."""
        active_statuses = ("watching", "confirmed", "entry")
        with self.session() as session:
            stmt = select(ScannerSignalRecord).order_by(ScannerSignalRecord.score.desc())
            if active_only:
                stmt = stmt.where(ScannerSignalRecord.status.in_(active_statuses))
            if min_score > 0:
                stmt = stmt.where(ScannerSignalRecord.score >= min_score)
            if limit is not None:
                stmt = stmt.limit(limit)
            return list(session.scalars(stmt).all())

    def delete_scanner_signal(self, symbol: str) -> None:
        """Entfernt ein Signal endgültig."""
        with self.session() as session:
            session.execute(
                delete(ScannerSignalRecord).where(ScannerSignalRecord.symbol == symbol)
            )

    def save_scanner_status(
        self,
        universe_size: int,
        scanned_symbols: int,
        signals_found: int,
        failed_symbols: int,
        duration_seconds: float,
        running: bool,
    ) -> None:
        """Aktualisiert die Metadaten des letzten Scan-Durchlaufs (Einzelzeile)."""
        with self.session() as session:
            session.merge(
                ScannerStatusRecord(
                    id=1,
                    last_scan_at=datetime.now(timezone.utc),
                    universe_size=universe_size,
                    scanned_symbols=scanned_symbols,
                    signals_found=signals_found,
                    failed_symbols=failed_symbols,
                    duration_seconds=duration_seconds,
                    running=1 if running else 0,
                )
            )

    def get_scanner_status(self) -> ScannerStatusRecord | None:
        """Metadaten des letzten Scan-Durchlaufs oder None."""
        with self.session() as session:
            return session.get(ScannerStatusRecord, 1)
