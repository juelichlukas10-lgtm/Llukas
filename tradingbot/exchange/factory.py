"""Exchange-Factory und -Registry.

Neue Börsen-Adapter werden per :func:`register_exchange` registriert und
sind damit ohne Änderungen am Kernsystem verfügbar. CCXT-basierte Börsen
(binance, bybit, okx, kraken, ...) werden automatisch über den
generischen :class:`CcxtExchangeAdapter` erzeugt.
"""

from __future__ import annotations

from typing import Callable

from tradingbot.core.config import Config, load_credentials
from tradingbot.core.enums import TradingMode
from tradingbot.core.exceptions import ConfigError, ExchangeAuthError
from tradingbot.core.logging import get_logger
from tradingbot.exchange.base import ExchangeAdapter
from tradingbot.exchange.ccxt_adapter import CcxtExchangeAdapter
from tradingbot.exchange.paper import PaperExchangeAdapter

logger = get_logger(__name__)

#: Registry benutzerdefinierter Adapter-Factories: name -> factory(config).
_REGISTRY: dict[str, Callable[[Config], ExchangeAdapter]] = {}

#: Von CCXT Pro unterstützte, getestete Börsen.
SUPPORTED_CCXT_EXCHANGES = ("binance", "bybit", "okx", "kraken")


def register_exchange(name: str) -> Callable[[Callable[[Config], ExchangeAdapter]], Callable[[Config], ExchangeAdapter]]:
    """Decorator zur Registrierung einer eigenen Exchange-Factory.

    Beispiel:
        >>> @register_exchange("myexchange")
        ... def build_my_exchange(config: Config) -> ExchangeAdapter:
        ...     return MyExchangeAdapter(...)

    Args:
        name: Eindeutiger Börsenname für die Konfiguration.

    Returns:
        Decorator, der die Factory registriert und unverändert zurückgibt.
    """

    def decorator(factory: Callable[[Config], ExchangeAdapter]) -> Callable[[Config], ExchangeAdapter]:
        key = name.lower()
        if key in _REGISTRY:
            logger.warning("Exchange-Factory '%s' wird überschrieben", key)
        _REGISTRY[key] = factory
        return factory

    return decorator


def _build_ccxt_adapter(config: Config, *, require_keys: bool) -> CcxtExchangeAdapter:
    """Erzeugt den CCXT-Adapter für die konfigurierte Börse."""
    exchange_name = config.trading.exchange.lower()
    credentials = load_credentials(exchange_name)
    if require_keys and not credentials.is_configured:
        raise ExchangeAuthError(
            f"Live-Trading auf {exchange_name} erfordert API-Keys in den Umgebungsvariablen "
            f"{exchange_name.upper()}_API_KEY / {exchange_name.upper()}_API_SECRET"
        )
    return CcxtExchangeAdapter(
        exchange_id=exchange_name,
        credentials=credentials,
        settings=config.exchange_settings(exchange_name),
        market_type=config.trading.market_type,
    )


def create_exchange(config: Config) -> ExchangeAdapter:
    """Erzeugt den Exchange-Adapter passend zu Konfiguration und Modus.

    * ``mode: paper`` – :class:`PaperExchangeAdapter` mit einem öffentlichen
      CCXT-Adapter als Live-Datenquelle (keine API-Keys erforderlich).
    * ``mode: live`` – :class:`CcxtExchangeAdapter` mit API-Keys aus den
      Umgebungsvariablen (Pflicht) und bestätigtem Live-Flag.
    * Eigene, via :func:`register_exchange` registrierte Börsen haben Vorrang.

    Args:
        config: Validierte Bot-Konfiguration.

    Returns:
        Einsatzbereiter (noch nicht verbundener) Exchange-Adapter.

    Raises:
        ConfigError: Bei unbekannter Börse.
        ExchangeAuthError: Bei fehlenden API-Keys im Live-Modus.
    """
    exchange_name = config.trading.exchange.lower()

    if exchange_name in _REGISTRY:
        logger.info("Erzeuge registrierten Custom-Adapter '%s'", exchange_name)
        return _REGISTRY[exchange_name](config)

    if exchange_name not in SUPPORTED_CCXT_EXCHANGES:
        # CCXT unterstützt weit mehr Börsen – unbekannte Namen dennoch versuchen,
        # aber mit klarer Fehlermeldung bei Nichtverfügbarkeit.
        import ccxt.pro as ccxtpro

        if not hasattr(ccxtpro, exchange_name):
            raise ConfigError(
                f"Unbekannte Börse '{exchange_name}'. Unterstützt: "
                f"{', '.join(SUPPORTED_CCXT_EXCHANGES)} sowie alle CCXT-Pro-Börsen "
                f"und via register_exchange() registrierte Adapter."
            )

    if config.trading.mode is TradingMode.LIVE:
        logger.warning("LIVE-TRADING aktiv auf %s – echte Orders werden platziert!", exchange_name)
        return _build_ccxt_adapter(config, require_keys=True)

    data_provider = _build_ccxt_adapter(config, require_keys=False)
    quote = _primary_quote_currency(config)
    logger.info("Paper-Trading-Modus aktiv (Datenquelle: %s, Quote: %s)", exchange_name, quote)
    return PaperExchangeAdapter(
        config=config.paper,
        data_provider=data_provider,
        quote_currency=quote,
    )


def _primary_quote_currency(config: Config) -> str:
    """Leitet die Quote-Währung aus dem ersten konfigurierten Symbol ab."""
    first_symbol = config.trading.symbols[0]
    if "/" in first_symbol:
        return first_symbol.split("/")[1].split(":")[0]
    return "USDT"
