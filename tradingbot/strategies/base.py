"""Basisklasse und Kontext für Strategie-Plugins.

Eine Strategie ist eine einzelne Python-Datei im Paket
``tradingbot.strategies``, die eine von :class:`Strategy` abgeleitete
Klasse enthält und sich per ``@register_strategy("name")`` registriert.
Strategien sind reine Signalgeber: Sie analysieren Kerzendaten und
liefern :class:`~tradingbot.core.models.Signal`-Objekte – über Risiko
und Ausführung entscheiden nachgelagerte Schichten.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable

import pandas as pd

from tradingbot.core.enums import PositionSide, SignalAction, Timeframe
from tradingbot.core.exceptions import StrategyError
from tradingbot.core.logging import get_logger
from tradingbot.core.models import OrderBook, Signal

logger = get_logger(__name__)


@dataclass(slots=True)
class StrategyContext:
    """Laufzeit-Kontext, den die Engine einer Strategie bereitstellt.

    Attributes:
        position_side: Callback ``symbol -> PositionSide | None`` – aktuelle
            Positionsrichtung des Bots für dieses Symbol.
        get_candles: Callback ``(symbol, timeframe) -> DataFrame`` für
            Multi-Timeframe-Strategien (kann leeren DataFrame liefern).
        get_order_book: Optionaler Callback ``symbol -> OrderBook | None``
            für Order-Flow-Strategien.
        extra: Beliebige Zusatzdaten (z. B. Funding-Rates).
    """

    position_side: Callable[[str], PositionSide | None] = lambda symbol: None
    get_candles: Callable[[str, Timeframe], pd.DataFrame] = (
        lambda symbol, timeframe: pd.DataFrame()
    )
    get_order_book: Callable[[str], OrderBook | None] = lambda symbol: None
    extra: dict[str, Any] = field(default_factory=dict)


class Strategy(ABC):
    """Abstrakte Basisklasse aller Handelsstrategien.

    Unterklassen definieren:
        * ``name`` – eindeutiger Registry-Name (Klassenattribut).
        * ``default_params`` – Standard-Parameter (Klassenattribut).
        * :meth:`generate_signal` – die eigentliche Signallogik.

    Args:
        symbols: Symbole, auf denen die Strategie arbeitet.
        timeframe: Haupt-Timeframe der Strategie.
        params: Parameter-Overrides; werden mit ``default_params`` gemerged.
    """

    #: Eindeutiger Name für Registry und Konfiguration.
    name: str = "abstract"
    #: Standard-Parameter; werden durch Konfiguration überschrieben.
    default_params: dict[str, Any] = {}

    def __init__(
        self,
        symbols: list[str],
        timeframe: Timeframe = Timeframe.M5,
        params: dict[str, Any] | None = None,
    ) -> None:
        if not symbols:
            raise StrategyError(f"Strategie '{self.name}' benötigt mindestens ein Symbol")
        self.symbols = list(symbols)
        self.timeframe = timeframe
        self.params: dict[str, Any] = {**self.default_params, **(params or {})}
        self._validate_params()

    # ------------------------------------------------------------------
    # Überschreibbare Hooks
    # ------------------------------------------------------------------

    def _validate_params(self) -> None:
        """Validiert die Parameter; Unterklassen können verschärfen.

        Raises:
            StrategyError: Bei ungültigen Parametern.
        """
        for key, value in self.params.items():
            if key.endswith("_period") and (not isinstance(value, int) or value < 1):
                raise StrategyError(
                    f"Strategie '{self.name}': Parameter '{key}' muss ein positiver Integer sein"
                )

    @property
    def required_history(self) -> int:
        """Mindestanzahl an Kerzen, die :meth:`generate_signal` benötigt."""
        periods = [v for k, v in self.params.items() if k.endswith("_period") and isinstance(v, int)]
        return max(periods, default=20) * 3

    @property
    def additional_timeframes(self) -> list[Timeframe]:
        """Zusätzliche Timeframes (für Multi-Timeframe-Strategien)."""
        return []

    @abstractmethod
    def generate_signal(
        self, df: pd.DataFrame, symbol: str, context: StrategyContext
    ) -> Signal | None:
        """Analysiert die Kerzendaten und liefert ggf. ein Signal.

        Args:
            df: OHLCV-DataFrame des Haupt-Timeframes (aufsteigend sortiert,
                letzte Zeile = jüngste abgeschlossene Kerze).
            symbol: Analysiertes Symbol.
            context: Laufzeit-Kontext (Positionen, weitere Timeframes, ...).

        Returns:
            Signal oder None (= kein Handlungsbedarf).
        """

    # ------------------------------------------------------------------
    # Komfort-Helfer für Unterklassen
    # ------------------------------------------------------------------

    def make_signal(
        self,
        action: SignalAction,
        symbol: str,
        df: pd.DataFrame,
        reason: str = "",
        confidence: float = 1.0,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        **metadata: Any,
    ) -> Signal:
        """Erzeugt ein Signal mit Preis/Zeitstempel der letzten Kerze."""
        last = df.iloc[-1]
        timestamp = df.index[-1]
        return Signal(
            action=action,
            symbol=symbol,
            strategy=self.name,
            timestamp=timestamp.to_pydatetime() if hasattr(timestamp, "to_pydatetime") else timestamp,
            price=float(last["close"]),
            confidence=max(0.0, min(1.0, confidence)),
            stop_loss=stop_loss,
            take_profit=take_profit,
            reason=reason,
            metadata=metadata,
        )

    def has_enough_history(self, df: pd.DataFrame) -> bool:
        """True, wenn genügend Kerzen für eine Auswertung vorliegen."""
        return len(df) >= self.required_history

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(name={self.name!r}, symbols={self.symbols}, "
            f"timeframe={self.timeframe.value}, params={self.params})"
        )
