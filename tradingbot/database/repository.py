"""Repository-Fassade über der SQLAlchemy-Persistenz.

Die Klasse :class:`Database` kapselt Engine, Session-Handling und alle
CRUD-Operationen. Unterstützt SQLite (Standard) und PostgreSQL über die
konfigurierte SQLAlchemy-URL.
"""

from __future__ import annotations

import traceback as tb_module
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

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

    def __init__(self, url: str = "sqlite:///storage/tradingbot.db", echo: bool = False) -> None:
        if url.startswith("sqlite:///"):
            db_path = Path(url.removeprefix("sqlite:///"))
            if db_path.parent != Path("."):
                db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._engine: Engine = create_engine(url, echo=echo, future=True)
            Base.metadata.create_all(self._engine)
        except Exception as exc:
            raise DatabaseError(f"Datenbank-Initialisierung fehlgeschlagen ({url}): {exc}") from exc
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
