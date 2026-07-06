"""SQLAlchemy-ORM-Modelle der Persistenzschicht.

Gespeichert werden Trades, Orders, Positionen, Performance-Snapshots,
Strategie-Metadaten, Fehlerlogs und Backtest-Ergebnisse. Die Modelle
sind bewusst von den Domänenmodellen (:mod:`tradingbot.core.models`)
getrennt, damit sich Persistenz- und Laufzeitschicht unabhängig
weiterentwickeln können.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Gemeinsame Basis aller ORM-Modelle."""


class TradeRecord(Base):
    """Abgeschlossener Round-Trip-Trade."""

    __tablename__ = "trades"
    __table_args__ = (
        Index("ix_trades_symbol_closed", "symbol", "closed_at"),
        Index("ix_trades_strategy", "strategy"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    exit_price: Mapped[float] = mapped_column(Float, nullable=False)
    pnl: Mapped[float] = mapped_column(Float, nullable=False)
    fees: Mapped[float] = mapped_column(Float, default=0.0)
    leverage: Mapped[float] = mapped_column(Float, default=1.0)
    strategy: Mapped[str] = mapped_column(String(64), default="")
    exit_reason: Mapped[str] = mapped_column(String(32), default="signal")
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OrderRecord(Base):
    """Order-Historie (alle jemals platzierten Orders)."""

    __tablename__ = "orders"
    __table_args__ = (Index("ix_orders_symbol_created", "symbol", "created_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    exchange_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    filled: Mapped[float] = mapped_column(Float, default=0.0)
    average_price: Mapped[float] = mapped_column(Float, default=0.0)
    fee: Mapped[float] = mapped_column(Float, default=0.0)
    strategy: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PositionRecord(Base):
    """Snapshot einer offenen Position (wird bei Schluss entfernt)."""

    __tablename__ = "positions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    leverage: Mapped[float] = mapped_column(Float, default=1.0)
    stop_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    take_profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    strategy: Mapped[str] = mapped_column(String(64), default="")
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PerformanceRecord(Base):
    """Periodischer Equity-/Performance-Snapshot."""

    __tablename__ = "performance"
    __table_args__ = (Index("ix_performance_timestamp", "timestamp"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    equity: Mapped[float] = mapped_column(Float, nullable=False)
    balance: Mapped[float] = mapped_column(Float, nullable=False)
    open_positions: Mapped[int] = mapped_column(Integer, default=0)
    daily_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    drawdown: Mapped[float] = mapped_column(Float, default=0.0)


class StrategyRecord(Base):
    """Metadaten und Parameter einer (aktiven) Strategie."""

    __tablename__ = "strategies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    symbols: Mapped[list] = mapped_column(JSON, default=list)
    timeframe: Mapped[str] = mapped_column(String(8), default="5m")
    enabled: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ErrorLogRecord(Base):
    """Persistierter Fehler für Diagnose und Dashboard."""

    __tablename__ = "error_logs"
    __table_args__ = (Index("ix_error_logs_timestamp", "timestamp"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    level: Mapped[str] = mapped_column(String(10), default="ERROR")
    module: Mapped[str] = mapped_column(String(64), default="")
    message: Mapped[str] = mapped_column(Text, nullable=False)
    traceback: Mapped[str] = mapped_column(Text, default="")


class BacktestRecord(Base):
    """Ergebnis eines Backtest-Laufs inklusive Kennzahlen."""

    __tablename__ = "backtests"
    __table_args__ = (Index("ix_backtests_strategy", "strategy"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    strategy: Mapped[str] = mapped_column(String(64), nullable=False)
    symbols: Mapped[list] = mapped_column(JSON, default=list)
    timeframe: Mapped[str] = mapped_column(String(8), default="5m")
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    initial_balance: Mapped[float] = mapped_column(Float, nullable=False)
    final_equity: Mapped[float] = mapped_column(Float, nullable=False)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
