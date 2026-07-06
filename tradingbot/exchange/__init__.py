"""Exchange-Schicht: einheitliche Abstraktion über mehrere Börsen.

Öffentliche API:
    * :class:`tradingbot.exchange.base.ExchangeAdapter` – abstrakte Basis.
    * :func:`tradingbot.exchange.factory.create_exchange` – Factory.
    * :func:`tradingbot.exchange.factory.register_exchange` – Registrierung
      neuer Adapter ohne Änderung am Kernsystem.
"""

from tradingbot.exchange.base import ExchangeAdapter
from tradingbot.exchange.factory import create_exchange, register_exchange

__all__ = ["ExchangeAdapter", "create_exchange", "register_exchange"]
