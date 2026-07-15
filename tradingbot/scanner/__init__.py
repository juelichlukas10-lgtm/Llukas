"""Buy-the-Dip-Marktscanner.

Eigenständiges Modul, das unabhängig vom Trading-Bot läuft und ein
großes Aktienuniversum permanent nach Buy-the-Dip-Gelegenheiten
durchsucht: intakter Aufwärtstrend, geordneter Rücksetzer in Richtung
einer relevanten Unterstützung, erste Stabilisierungsanzeichen.

Bausteine:
    * :mod:`~tradingbot.scanner.models` – Signal- und Status-Modelle
    * :mod:`~tradingbot.scanner.universe` – Ticker-Universen
    * :mod:`~tradingbot.scanner.data_provider` – Kursdaten (yfinance)
    * :mod:`~tradingbot.scanner.detector` – Mustererkennung
    * :mod:`~tradingbot.scanner.scoring` – Score 0–100
    * :mod:`~tradingbot.scanner.engine` – asynchrone Scan-Loop
"""

from tradingbot.scanner.models import DipSignal, SetupStatus

__all__ = ["DipSignal", "SetupStatus"]
