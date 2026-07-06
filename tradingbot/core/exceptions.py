"""Zentrale Exception-Hierarchie des Trading-Bots.

Alle bot-spezifischen Fehler erben von :class:`TradingBotError`, sodass
aufrufende Schichten gezielt zwischen internen Fehlern und Fremdfehlern
unterscheiden können.
"""

from __future__ import annotations


class TradingBotError(Exception):
    """Basisklasse aller Bot-Fehler."""


class ConfigError(TradingBotError):
    """Fehlerhafte oder unvollständige Konfiguration."""


class ExchangeError(TradingBotError):
    """Allgemeiner Fehler in der Exchange-Schicht."""


class ExchangeConnectionError(ExchangeError):
    """Netzwerk- oder Verbindungsfehler zur Börse."""


class ExchangeAuthError(ExchangeError):
    """Ungültige oder fehlende API-Zugangsdaten."""


class RateLimitError(ExchangeError):
    """Rate-Limit der Börse wurde erreicht."""


class OrderError(TradingBotError):
    """Fehler beim Erstellen, Ändern oder Stornieren einer Order."""


class InsufficientFundsError(OrderError):
    """Unzureichendes Guthaben für die angeforderte Order."""


class DataError(TradingBotError):
    """Fehler bei Beschaffung oder Verarbeitung von Marktdaten."""


class StrategyError(TradingBotError):
    """Fehler innerhalb einer Strategie oder des Plugin-Systems."""


class RiskError(TradingBotError):
    """Verstoß gegen Risiko-Regeln (z. B. Limits überschritten)."""


class BacktestError(TradingBotError):
    """Fehler während eines Backtests oder einer Optimierung."""


class DatabaseError(TradingBotError):
    """Fehler in der Persistenzschicht."""


class NotificationError(TradingBotError):
    """Fehler beim Versand einer Benachrichtigung."""
