"""Domänenmodelle des Buy-the-Dip-Scanners."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from tradingbot.core.models import utc_now


class SetupStatus(StrEnum):
    """Lebenszyklus eines Buy-the-Dip-Setups.

    * ``WATCHING`` – Rücksetzer im Aufwärtstrend läuft, Kurs nähert sich
      einer Unterstützung; noch keine Stabilisierung.
    * ``CONFIRMED`` – erste Stabilisierungsanzeichen (bullische Kerze,
      RSI dreht, Kaufvolumen zieht an).
    * ``ENTRY`` – Einstiegssignal: Kurs dreht nach oben und überwindet
      das kurzfristige Hoch bzw. die EMA20.
    * ``TARGET_REACHED`` – erstes Kursziel erreicht.
    * ``INVALIDATED`` – Unterstützung nachhaltig gebrochen oder
      übergeordneter Trend beschädigt.
    """

    WATCHING = "watching"
    CONFIRMED = "confirmed"
    ENTRY = "entry"
    TARGET_REACHED = "target_reached"
    INVALIDATED = "invalidated"

    @property
    def is_active(self) -> bool:
        """True, solange das Setup handelbar/beobachtenswert ist."""
        return self in (SetupStatus.WATCHING, SetupStatus.CONFIRMED, SetupStatus.ENTRY)


class SupportType(StrEnum):
    """Art der Unterstützung, an der der Rücksetzer stattfindet."""

    EMA20 = "ema20"
    EMA50 = "ema50"
    EMA100 = "ema100"
    EMA200 = "ema200"
    BREAKOUT_LEVEL = "breakout_level"
    FIB_382 = "fib_382"
    FIB_500 = "fib_500"
    FIB_618 = "fib_618"


@dataclass(slots=True)
class DipSignal:
    """Ein erkanntes Buy-the-Dip-Setup mit allen Anzeige- und Handelsdaten.

    Attributes:
        symbol: Tickersymbol (z. B. ``"AAPL"``).
        name: Firmenname (Fallback: Symbol).
        status: Aktueller Setup-Status.
        score: Gesamtbewertung 0–100.
        price: Letzter Schlusskurs.
        change_pct: Tagesveränderung als Bruchteil.
        recent_high: Bezugshoch des Rücksetzers.
        drawdown_pct: Abstand zum Hoch als Bruchteil (positiv = unter Hoch).
        support_type: Art der nächstgelegenen Unterstützung.
        support_level: Preisniveau der Unterstützung.
        support_distance_pct: Abstand Kurs -> Unterstützung als Bruchteil
            (positiv = Kurs über der Unterstützung).
        trend_strength: Trendqualität in [0, 1].
        rsi: Aktueller RSI(14).
        volume: Letztes Tagesvolumen.
        volume_ratio: Volumen relativ zum 20-Tage-Durchschnitt.
        relative_strength: Outperformance vs. Benchmark über ~3 Monate
            (Bruchteil; 0.05 = 5 Punkte besser als der Markt).
        atr: ATR(14) in Kurspunkten.
        entry_price: Vorgeschlagene Einstiegslinie (Mikro-Breakout).
        stop_loss: Vorgeschlagener Stop-Loss.
        target_1: Erstes Kursziel (i. d. R. das Bezugshoch).
        target_2: Zweites Kursziel (Extension).
        risk_reward: Chance-Risiko-Verhältnis auf Ziel 1.
        detected_at: Zeitpunkt der Ersterkennung.
        updated_at: Zeitpunkt der letzten Aktualisierung.
        score_breakdown: Einzelkomponenten des Scores (für das Dashboard).
    """

    symbol: str
    name: str
    status: SetupStatus
    score: float
    price: float
    change_pct: float
    recent_high: float
    drawdown_pct: float
    support_type: SupportType
    support_level: float
    support_distance_pct: float
    trend_strength: float
    rsi: float
    volume: float
    volume_ratio: float
    relative_strength: float
    atr: float
    entry_price: float
    stop_loss: float
    target_1: float
    target_2: float
    risk_reward: float
    detected_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    score_breakdown: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisierbare Repräsentation (für DB/Dashboard)."""
        return {
            "symbol": self.symbol,
            "name": self.name,
            "status": self.status.value,
            "score": round(self.score, 1),
            "price": round(self.price, 4),
            "change_pct": round(self.change_pct, 6),
            "recent_high": round(self.recent_high, 4),
            "drawdown_pct": round(self.drawdown_pct, 6),
            "support_type": self.support_type.value,
            "support_level": round(self.support_level, 4),
            "support_distance_pct": round(self.support_distance_pct, 6),
            "trend_strength": round(self.trend_strength, 4),
            "rsi": round(self.rsi, 2),
            "volume": self.volume,
            "volume_ratio": round(self.volume_ratio, 3),
            "relative_strength": round(self.relative_strength, 6),
            "atr": round(self.atr, 4),
            "entry_price": round(self.entry_price, 4),
            "stop_loss": round(self.stop_loss, 4),
            "target_1": round(self.target_1, 4),
            "target_2": round(self.target_2, 4),
            "risk_reward": round(self.risk_reward, 2),
            "detected_at": self.detected_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "score_breakdown": {k: round(v, 2) for k, v in self.score_breakdown.items()},
        }


@dataclass(frozen=True, slots=True)
class ScanCycleStats:
    """Kennzahlen eines abgeschlossenen Scan-Durchlaufs."""

    scanned_symbols: int
    signals_found: int
    failed_symbols: int
    duration_seconds: float
    finished_at: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class ScannerPosition:
    """Offene Paper-Trading-Position des Scanners (long-only, eine je Symbol).

    Attributes:
        symbol: Tickersymbol.
        name: Anzeigename.
        amount: Anzahl Aktien.
        entry_price: Durchschnittlicher Einstiegspreis.
        stop_loss: Aktueller Stop-Loss (wird nach Teilverkauf auf Einstand gezogen).
        target_1: Erstes Kursziel (löst bei Erreichen einen Teilverkauf aus).
        target_2: Zweites Kursziel (Exit der Restposition).
        opened_at: Eröffnungszeitpunkt.
        partial_exit_done: True, wenn Ziel 1 bereits realisiert wurde.
        fees_paid: Bisher gezahlte Gebühren (Einstieg + evtl. Teilverkauf).
        realized_pnl: Bereits realisierter PnL aus Teilverkäufen.
    """

    symbol: str
    name: str
    amount: float
    entry_price: float
    stop_loss: float
    target_1: float
    target_2: float
    opened_at: datetime = field(default_factory=utc_now)
    partial_exit_done: bool = False
    fees_paid: float = 0.0
    realized_pnl: float = 0.0

    def unrealized_pnl(self, current_price: float) -> float:
        """Unrealisierter Gewinn/Verlust zum aktuellen Kurs."""
        return (current_price - self.entry_price) * self.amount


@dataclass(slots=True)
class ScannerTrade:
    """Abgeschlossener (Teil-)Trade des Scanner-Paper-Depots.

    Attributes:
        symbol: Tickersymbol.
        amount: Verkaufte Menge (kann eine Teilmenge der Position sein).
        entry_price: Einstiegspreis.
        exit_price: Ausstiegspreis.
        pnl: Realisierter Gewinn/Verlust nach Gebühren.
        fees: Gebühren dieses Fills.
        exit_reason: ``target_1`` | ``target_2`` | ``stop_loss`` | ``invalidated``.
        opened_at: Eröffnungszeitpunkt der (Ursprungs-)Position.
        closed_at: Zeitpunkt dieses (Teil-)Exits.
    """

    symbol: str
    amount: float
    entry_price: float
    exit_price: float
    pnl: float
    fees: float
    exit_reason: str
    opened_at: datetime
    closed_at: datetime = field(default_factory=utc_now)

    @property
    def is_win(self) -> bool:
        """True bei positivem PnL."""
        return self.pnl > 0


@dataclass(frozen=True, slots=True)
class ScannerPortfolioSnapshot:
    """Kennzahlen des Scanner-Paper-Depots für das Dashboard."""

    cash: float
    equity: float
    open_positions: int
    total_trades: int
    win_rate: float
    total_pnl: float
