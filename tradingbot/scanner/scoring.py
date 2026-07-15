"""Bewertung von Buy-the-Dip-Setups (Score 0–100).

Der Score setzt sich aus gewichteten Komponenten zusammen; die
Gewichtung ist konfigurierbar. Jede Komponente wird auf [0, 1]
normalisiert und mit ihrem Maximalbeitrag multipliziert. Die Summe der
Maximalbeiträge ergibt 100.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tradingbot.scanner.models import SetupStatus


@dataclass(frozen=True, slots=True)
class ScoreFactors:
    """Eingangsgrößen der Bewertung (vom Detektor geliefert).

    Attributes:
        trend_strength: Trendqualität in [0, 1].
        drawdown_pct: Rücksetzer vom Hoch (Bruchteil).
        support_distance_pct: Abstand zur Unterstützung (Bruchteil,
            positiv = darüber).
        orderly: True, wenn der Rücksetzer geordnet verlief.
        down_volume_ratio: Abwärts-/Aufwärts-Volumen im Rücksetzer
            (kleiner = nachlassender Verkaufsdruck).
        stabilization: Stabilisierungsgrad in [0, 1].
        rsi: Aktueller RSI(14).
        relative_strength: Outperformance vs. Benchmark (Bruchteil).
        risk_reward: Chance-Risiko-Verhältnis auf Ziel 1.
        status: Setup-Status (Bestätigung erhöht den Score).
    """

    trend_strength: float
    drawdown_pct: float
    support_distance_pct: float
    orderly: bool
    down_volume_ratio: float
    stabilization: float
    rsi: float
    relative_strength: float
    risk_reward: float
    status: SetupStatus


@dataclass(slots=True)
class ScoreWeights:
    """Maximalbeiträge der Komponenten; Summe = 100."""

    trend: float = 25.0
    pullback: float = 15.0
    support: float = 15.0
    volume: float = 10.0
    stabilization: float = 10.0
    relative_strength: float = 10.0
    risk_reward: float = 10.0
    status_bonus: float = 5.0

    def total(self) -> float:
        return (
            self.trend + self.pullback + self.support + self.volume
            + self.stabilization + self.relative_strength + self.risk_reward
            + self.status_bonus
        )


class DipScorer:
    """Berechnet den Gesamtscore eines Setups.

    Args:
        weights: Komponenten-Gewichtung (Standard summiert auf 100).
        ideal_dip: Rücksetzer-Tiefe mit maximaler Punktzahl (Bruchteil).
    """

    def __init__(self, weights: ScoreWeights | None = None, ideal_dip: float = 0.07) -> None:
        self.weights = weights or ScoreWeights()
        self.ideal_dip = ideal_dip

    def score(self, factors: ScoreFactors) -> tuple[float, dict[str, float]]:
        """Bewertet ein Setup.

        Args:
            factors: Eingangsgrößen aus der Mustererkennung.

        Returns:
            (Gesamtscore 0–100, Aufschlüsselung je Komponente).
        """
        w = self.weights
        breakdown = {
            "trend": w.trend * _clamp(factors.trend_strength),
            "pullback": w.pullback * self._pullback_quality(factors),
            "support": w.support * self._support_quality(factors.support_distance_pct),
            "volume": w.volume * self._volume_quality(factors.down_volume_ratio),
            "stabilization": w.stabilization * _clamp(factors.stabilization),
            "relative_strength": w.relative_strength * self._rs_quality(factors.relative_strength),
            "risk_reward": w.risk_reward * self._rr_quality(factors.risk_reward),
            "status_bonus": w.status_bonus * self._status_bonus(factors.status),
        }
        total = sum(breakdown.values()) * (100.0 / self.weights.total())
        return _clamp(total, upper=100.0), breakdown

    # ------------------------------------------------------------------
    # Komponenten-Normalisierung (alle liefern [0, 1])
    # ------------------------------------------------------------------

    def _pullback_quality(self, factors: ScoreFactors) -> float:
        """Ideal ist ein moderater, geordneter Rücksetzer nahe ``ideal_dip``."""
        # Dreiecksfunktion: 1.0 am Ideal, linear fallend zu den Rändern.
        dip = factors.drawdown_pct
        if dip <= 0:
            depth_quality = 0.0
        elif dip <= self.ideal_dip:
            depth_quality = dip / self.ideal_dip
        else:
            # Bis zum Dreifachen des Ideals linear auf 0 fallend.
            depth_quality = max(0.0, 1.0 - (dip - self.ideal_dip) / (2.0 * self.ideal_dip))
        orderly_quality = 1.0 if factors.orderly else 0.35
        return depth_quality * orderly_quality

    @staticmethod
    def _support_quality(distance: float) -> float:
        """Je näher an der Unterstützung (ohne klaren Bruch), desto besser."""
        if distance < -0.02:
            return 0.0
        return _clamp(1.0 - abs(distance) / 0.04)

    @staticmethod
    def _volume_quality(down_volume_ratio: float) -> float:
        """Nachlassender Verkaufsdruck: Ratio 0.5 -> 1.0 Punkte, 1.5 -> 0."""
        return _clamp((1.5 - down_volume_ratio) / 1.0)

    @staticmethod
    def _rs_quality(relative_strength: float) -> float:
        """Relative Stärke: -10 % -> 0, 0 % -> 0.5, +10 % -> 1.0."""
        return _clamp(0.5 + relative_strength / 0.20)

    @staticmethod
    def _rr_quality(risk_reward: float) -> float:
        """Chance-Risiko: 1.0 -> 0.25, 2.0 -> 0.5, >= 4.0 -> 1.0."""
        return _clamp(risk_reward / 4.0)

    @staticmethod
    def _status_bonus(status: SetupStatus) -> float:
        return {
            SetupStatus.WATCHING: 0.0,
            SetupStatus.CONFIRMED: 0.6,
            SetupStatus.ENTRY: 1.0,
            SetupStatus.TARGET_REACHED: 1.0,
            SetupStatus.INVALIDATED: 0.0,
        }.get(status, 0.0)


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    """Begrenzt einen Wert auf [lower, upper]."""
    return max(lower, min(upper, value))
