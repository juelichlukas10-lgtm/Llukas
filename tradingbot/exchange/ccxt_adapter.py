"""CCXT-basierter Exchange-Adapter (REST + WebSocket).

Kapselt die `ccxt.pro`-Bibliothek hinter der einheitlichen
:class:`~tradingbot.exchange.base.ExchangeAdapter`-Schnittstelle.
Unterstützt u. a. Binance, Bybit, OKX und Kraken; weitere von CCXT
unterstützte Börsen funktionieren ohne Codeänderung über die Factory.

Eigenschaften:
    * API-Keys ausschließlich aus Umgebungsvariablen.
    * Eingebautes Rate-Limiting von CCXT.
    * Automatischer Reconnect aller ``watch_*``-Streams.
    * Einheitliches Fehler-Mapping auf die Bot-Exception-Hierarchie.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, AsyncIterator

import ccxt.pro as ccxtpro
from ccxt.base import errors as ccxt_errors

from tradingbot.core.config import ExchangeCredentials, ExchangeSettings
from tradingbot.core.enums import MarketType, OrderSide, OrderStatus, OrderType, Timeframe
from tradingbot.core.exceptions import (
    ExchangeAuthError,
    ExchangeConnectionError,
    ExchangeError,
    InsufficientFundsError,
    OrderError,
    RateLimitError,
)
from tradingbot.core.logging import get_logger
from tradingbot.core.models import (
    Balance,
    Candle,
    FundingRate,
    OpenInterest,
    Order,
    OrderBook,
    OrderBookLevel,
    Ticker,
    TradeTick,
)
from tradingbot.exchange.base import ExchangeAdapter

logger = get_logger(__name__)

#: Wartezeit zwischen Reconnect-Versuchen der Watch-Streams (Sekunden).
_RECONNECT_DELAY = 5.0

_STATUS_MAP: dict[str, OrderStatus] = {
    "open": OrderStatus.OPEN,
    "closed": OrderStatus.FILLED,
    "canceled": OrderStatus.CANCELED,
    "cancelled": OrderStatus.CANCELED,
    "expired": OrderStatus.EXPIRED,
    "rejected": OrderStatus.REJECTED,
}


def _ms_to_dt(ms: int | float | None) -> datetime:
    """Unix-Millisekunden in eine UTC-Datetime konvertieren (None = jetzt)."""
    if ms is None:
        return datetime.now(timezone.utc)
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)


def translate_ccxt_error(exc: BaseException) -> Exception:
    """Mappt eine CCXT-Exception auf die Bot-Exception-Hierarchie."""
    if isinstance(exc, ccxt_errors.AuthenticationError):
        return ExchangeAuthError(str(exc))
    if isinstance(exc, ccxt_errors.RateLimitExceeded):
        return RateLimitError(str(exc))
    if isinstance(exc, ccxt_errors.InsufficientFunds):
        return InsufficientFundsError(str(exc))
    if isinstance(exc, (ccxt_errors.InvalidOrder, ccxt_errors.OrderNotFound)):
        return OrderError(str(exc))
    if isinstance(exc, ccxt_errors.NetworkError):
        return ExchangeConnectionError(str(exc))
    if isinstance(exc, ccxt_errors.BaseError):
        return ExchangeError(str(exc))
    return ExchangeError(f"Unerwarteter Fehler: {exc}")


class CcxtExchangeAdapter(ExchangeAdapter):
    """Adapter für alle von CCXT Pro unterstützten Börsen.

    Args:
        exchange_id: CCXT-Börsen-ID (z. B. ``"binance"``, ``"bybit"``).
        credentials: API-Zugangsdaten (leer = nur öffentliche Endpunkte).
        settings: Nicht-geheime Börsen-Einstellungen (Testnet, Rate-Limit).
        market_type: Spot- oder Futures-Märkte.
    """

    def __init__(
        self,
        exchange_id: str,
        credentials: ExchangeCredentials | None = None,
        settings: ExchangeSettings | None = None,
        market_type: MarketType = MarketType.SPOT,
    ) -> None:
        self.name = exchange_id
        self._credentials = credentials or ExchangeCredentials()
        self._settings = settings or ExchangeSettings()
        self._market_type = market_type
        self._client = self._build_client()
        self._connected = False

    # ------------------------------------------------------------------
    # Aufbau / Lifecycle
    # ------------------------------------------------------------------

    def _build_client(self) -> Any:
        """Erzeugt und konfiguriert die CCXT-Pro-Client-Instanz."""
        if not hasattr(ccxtpro, self.name):
            raise ExchangeError(f"Börse '{self.name}' wird von CCXT Pro nicht unterstützt")
        exchange_class = getattr(ccxtpro, self.name)
        options: dict[str, Any] = dict(self._settings.options)
        options.setdefault(
            "defaultType", "swap" if self._market_type is MarketType.FUTURES else "spot"
        )
        config: dict[str, Any] = {
            "enableRateLimit": self._settings.rate_limit,
            "options": options,
        }
        if self._credentials.api_key:
            config["apiKey"] = self._credentials.api_key
        if self._credentials.api_secret:
            config["secret"] = self._credentials.api_secret
        if self._credentials.password:
            config["password"] = self._credentials.password
        client = exchange_class(config)
        if self._settings.testnet:
            client.set_sandbox_mode(True)
        return client

    async def connect(self) -> None:
        """Lädt die Markt-Metadaten der Börse."""
        try:
            await self._client.load_markets()
            self._connected = True
            logger.info("Verbunden mit %s (%d Märkte)", self.name, len(self._client.markets))
        except Exception as exc:
            raise translate_ccxt_error(exc) from exc

    async def close(self) -> None:
        """Schließt REST-Session und alle WebSocket-Verbindungen."""
        try:
            await self._client.close()
        except Exception:
            logger.exception("Fehler beim Schließen der Verbindung zu %s", self.name)
        finally:
            self._connected = False

    # ------------------------------------------------------------------
    # Marktdaten (REST)
    # ------------------------------------------------------------------

    async def fetch_ticker(self, symbol: str) -> Ticker:
        try:
            raw = await self._client.fetch_ticker(symbol)
        except Exception as exc:
            raise translate_ccxt_error(exc) from exc
        return self._parse_ticker(symbol, raw)

    async def fetch_order_book(self, symbol: str, limit: int = 25) -> OrderBook:
        try:
            raw = await self._client.fetch_order_book(symbol, limit)
        except Exception as exc:
            raise translate_ccxt_error(exc) from exc
        return self._parse_order_book(symbol, raw)

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: Timeframe,
        since: int | None = None,
        limit: int = 500,
    ) -> list[Candle]:
        try:
            raw = await self._client.fetch_ohlcv(symbol, timeframe.value, since=since, limit=limit)
        except Exception as exc:
            raise translate_ccxt_error(exc) from exc
        return [self._parse_candle(symbol, timeframe, row) for row in raw]

    async def fetch_trades(self, symbol: str, limit: int = 100) -> list[TradeTick]:
        try:
            raw = await self._client.fetch_trades(symbol, limit=limit)
        except Exception as exc:
            raise translate_ccxt_error(exc) from exc
        return [self._parse_trade(symbol, item) for item in raw]

    async def fetch_funding_rate(self, symbol: str) -> FundingRate | None:
        if self._market_type is not MarketType.FUTURES or not self._client.has.get("fetchFundingRate"):
            return None
        try:
            raw = await self._client.fetch_funding_rate(symbol)
        except Exception as exc:
            raise translate_ccxt_error(exc) from exc
        next_time = raw.get("fundingDatetime") or raw.get("nextFundingDatetime")
        return FundingRate(
            symbol=symbol,
            timestamp=_ms_to_dt(raw.get("timestamp")),
            rate=float(raw.get("fundingRate") or 0.0),
            next_funding_time=(
                datetime.fromisoformat(next_time.replace("Z", "+00:00")) if next_time else None
            ),
        )

    async def fetch_open_interest(self, symbol: str) -> OpenInterest | None:
        if self._market_type is not MarketType.FUTURES or not self._client.has.get("fetchOpenInterest"):
            return None
        try:
            raw = await self._client.fetch_open_interest(symbol)
        except Exception as exc:
            raise translate_ccxt_error(exc) from exc
        return OpenInterest(
            symbol=symbol,
            timestamp=_ms_to_dt(raw.get("timestamp")),
            open_interest=float(raw.get("openInterestAmount") or 0.0),
            open_interest_value=(
                float(raw["openInterestValue"]) if raw.get("openInterestValue") is not None else None
            ),
        )

    # ------------------------------------------------------------------
    # Konto & Orders (REST)
    # ------------------------------------------------------------------

    async def fetch_balance(self) -> dict[str, Balance]:
        try:
            raw = await self._client.fetch_balance()
        except Exception as exc:
            raise translate_ccxt_error(exc) from exc
        balances: dict[str, Balance] = {}
        for currency, total in (raw.get("total") or {}).items():
            if total is None:
                continue
            free = (raw.get("free") or {}).get(currency) or 0.0
            used = (raw.get("used") or {}).get(currency) or 0.0
            balances[currency] = Balance(currency=currency, free=float(free), used=float(used))
        return balances

    async def create_order(self, order: Order) -> Order:
        ccxt_type, params = self._map_order_type(order)
        price = order.price if ccxt_type == "limit" else None
        try:
            raw = await self._client.create_order(
                order.symbol, ccxt_type, order.side.value, order.amount, price, params
            )
        except Exception as exc:
            raise translate_ccxt_error(exc) from exc
        self._apply_exchange_order(order, raw)
        logger.info(
            "Order platziert: %s %s %s %.8f @ %s (exchange_id=%s)",
            self.name,
            order.side.value,
            order.symbol,
            order.amount,
            order.price if order.price is not None else "market",
            order.exchange_id,
        )
        return order

    async def cancel_order(self, order: Order) -> Order:
        if not order.exchange_id:
            raise OrderError(f"Order {order.id} hat keine Exchange-ID und kann nicht storniert werden")
        try:
            await self._client.cancel_order(order.exchange_id, order.symbol)
        except Exception as exc:
            raise translate_ccxt_error(exc) from exc
        order.status = OrderStatus.CANCELED
        return order

    async def fetch_order(self, order: Order) -> Order:
        if not order.exchange_id:
            raise OrderError(f"Order {order.id} hat keine Exchange-ID")
        try:
            raw = await self._client.fetch_order(order.exchange_id, order.symbol)
        except Exception as exc:
            raise translate_ccxt_error(exc) from exc
        self._apply_exchange_order(order, raw)
        return order

    async def fetch_open_orders(self, symbol: str | None = None) -> list[Order]:
        try:
            raw_orders = await self._client.fetch_open_orders(symbol)
        except Exception as exc:
            raise translate_ccxt_error(exc) from exc
        result: list[Order] = []
        for raw in raw_orders:
            order = Order(
                symbol=raw.get("symbol", symbol or ""),
                side=OrderSide(raw.get("side", "buy")),
                type=self._reverse_order_type(raw),
                amount=float(raw.get("amount") or 0.0),
                price=float(raw["price"]) if raw.get("price") else None,
            )
            self._apply_exchange_order(order, raw)
            result.append(order)
        return result

    async def set_leverage(self, symbol: str, leverage: float) -> None:
        if self._market_type is not MarketType.FUTURES:
            return
        if not self._client.has.get("setLeverage"):
            logger.warning("%s unterstützt setLeverage nicht", self.name)
            return
        try:
            await self._client.set_leverage(int(leverage), symbol)
        except Exception as exc:
            raise translate_ccxt_error(exc) from exc

    # ------------------------------------------------------------------
    # Live-Streams (WebSocket) – mit automatischem Reconnect
    # ------------------------------------------------------------------

    async def watch_ticker(self, symbol: str) -> AsyncIterator[Ticker]:
        async for raw in self._watch_loop("watch_ticker", symbol):
            yield self._parse_ticker(symbol, raw)

    async def watch_candles(self, symbol: str, timeframe: Timeframe) -> AsyncIterator[Candle]:
        async for raw in self._watch_loop("watch_ohlcv", symbol, timeframe.value):
            # watch_ohlcv liefert eine Liste von Kerzen; die letzte ist aktuell.
            for row in raw:
                yield self._parse_candle(symbol, timeframe, row)

    async def watch_order_book(self, symbol: str, limit: int = 25) -> AsyncIterator[OrderBook]:
        async for raw in self._watch_loop("watch_order_book", symbol, limit):
            yield self._parse_order_book(symbol, raw)

    async def watch_trades(self, symbol: str) -> AsyncIterator[TradeTick]:
        async for raw in self._watch_loop("watch_trades", symbol):
            for item in raw:
                yield self._parse_trade(symbol, item)

    async def _watch_loop(self, method_name: str, *args: Any) -> AsyncIterator[Any]:
        """Generische Watch-Schleife mit Reconnect bei Verbindungsverlust.

        Netzwerkfehler führen zu Warnung + Wartezeit + erneutem Versuch;
        Authentifizierungs- und sonstige fatale Fehler werden propagiert.
        """
        import asyncio

        method = getattr(self._client, method_name, None)
        if method is None:
            raise ExchangeError(f"{self.name} unterstützt {method_name} nicht")
        while True:
            try:
                result = await method(*args)
                yield result
            except ccxt_errors.AuthenticationError as exc:
                raise ExchangeAuthError(str(exc)) from exc
            except ccxt_errors.NetworkError as exc:
                logger.warning(
                    "WebSocket %s(%s) unterbrochen: %s – Reconnect in %.0fs",
                    method_name,
                    args,
                    exc,
                    _RECONNECT_DELAY,
                )
                await asyncio.sleep(_RECONNECT_DELAY)
            except ccxt_errors.BaseError as exc:
                logger.error("WebSocket %s(%s) Fehler: %s – Reconnect in %.0fs",
                             method_name, args, exc, _RECONNECT_DELAY)
                await asyncio.sleep(_RECONNECT_DELAY)

    # ------------------------------------------------------------------
    # Markt-Metadaten & Präzision
    # ------------------------------------------------------------------

    def market_info(self, symbol: str) -> dict[str, Any]:
        market = self._client.markets.get(symbol) if self._client.markets else None
        if market is None:
            raise ExchangeError(
                f"Unbekannter Markt '{symbol}' auf {self.name} – wurde connect() aufgerufen?"
            )
        return market

    def amount_to_precision(self, symbol: str, amount: float) -> float:
        try:
            return float(self._client.amount_to_precision(symbol, amount))
        except Exception:
            return round(amount, 8)

    def price_to_precision(self, symbol: str, price: float) -> float:
        try:
            return float(self._client.price_to_precision(symbol, price))
        except Exception:
            return round(price, 8)

    # ------------------------------------------------------------------
    # Parsing-Helfer
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_ticker(symbol: str, raw: dict[str, Any]) -> Ticker:
        last = float(raw.get("last") or raw.get("close") or 0.0)
        return Ticker(
            symbol=symbol,
            timestamp=_ms_to_dt(raw.get("timestamp")),
            bid=float(raw.get("bid") or last),
            ask=float(raw.get("ask") or last),
            last=last,
            volume_24h=float(raw.get("baseVolume") or 0.0),
        )

    @staticmethod
    def _parse_order_book(symbol: str, raw: dict[str, Any]) -> OrderBook:
        bids = tuple(
            OrderBookLevel(price=float(p), amount=float(a)) for p, a, *_ in raw.get("bids", [])
        )
        asks = tuple(
            OrderBookLevel(price=float(p), amount=float(a)) for p, a, *_ in raw.get("asks", [])
        )
        return OrderBook(symbol=symbol, timestamp=_ms_to_dt(raw.get("timestamp")), bids=bids, asks=asks)

    @staticmethod
    def _parse_candle(symbol: str, timeframe: Timeframe, row: list[Any]) -> Candle:
        return Candle(
            symbol=symbol,
            timeframe=timeframe,
            timestamp=_ms_to_dt(row[0]),
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume=float(row[5]),
        )

    @staticmethod
    def _parse_trade(symbol: str, raw: dict[str, Any]) -> TradeTick:
        return TradeTick(
            symbol=symbol,
            timestamp=_ms_to_dt(raw.get("timestamp")),
            price=float(raw.get("price") or 0.0),
            amount=float(raw.get("amount") or 0.0),
            side=OrderSide(raw.get("side") or "buy"),
        )

    def _map_order_type(self, order: Order) -> tuple[str, dict[str, Any]]:
        """Mappt den internen Ordertyp auf CCXT-Typ + Parameter."""
        params: dict[str, Any] = {}
        if order.reduce_only:
            params["reduceOnly"] = True
        if order.post_only:
            params["postOnly"] = True

        match order.type:
            case OrderType.MARKET:
                return "market", params
            case OrderType.LIMIT:
                if order.price is None:
                    raise OrderError("Limit-Order benötigt einen Preis")
                return "limit", params
            case OrderType.STOP_MARKET:
                if order.stop_price is None:
                    raise OrderError("Stop-Market-Order benötigt stop_price")
                params["triggerPrice"] = order.stop_price
                return "market", params
            case OrderType.STOP_LIMIT:
                if order.stop_price is None or order.price is None:
                    raise OrderError("Stop-Limit-Order benötigt stop_price und price")
                params["triggerPrice"] = order.stop_price
                return "limit", params
            case OrderType.TRAILING_STOP:
                if order.trailing_delta is None:
                    raise OrderError("Trailing-Stop-Order benötigt trailing_delta")
                # CCXT-einheitlicher Parameter; nicht jede Börse unterstützt ihn nativ.
                params["trailingPercent"] = order.trailing_delta * 100.0
                params.setdefault("reduceOnly", True)
                return "market", params
        raise OrderError(f"Unbekannter Ordertyp: {order.type}")

    @staticmethod
    def _reverse_order_type(raw: dict[str, Any]) -> OrderType:
        """Bestimmt den internen Ordertyp aus einer CCXT-Order."""
        raw_type = (raw.get("type") or "limit").lower()
        has_trigger = bool(raw.get("triggerPrice") or raw.get("stopPrice"))
        if has_trigger:
            return OrderType.STOP_LIMIT if raw_type == "limit" else OrderType.STOP_MARKET
        if raw_type == "market":
            return OrderType.MARKET
        return OrderType.LIMIT

    @staticmethod
    def _apply_exchange_order(order: Order, raw: dict[str, Any]) -> None:
        """Überträgt Felder einer CCXT-Order auf die interne Order."""
        from tradingbot.core.models import utc_now

        if raw.get("id"):
            order.exchange_id = str(raw["id"])
        status = raw.get("status")
        filled = float(raw.get("filled") or 0.0)
        if status in _STATUS_MAP:
            order.status = _STATUS_MAP[status]
        elif filled > 0:
            order.status = OrderStatus.PARTIALLY_FILLED
        elif order.status is OrderStatus.NEW:
            order.status = OrderStatus.OPEN
        if filled > 0:
            order.filled = filled
            if raw.get("average"):
                order.average_price = float(raw["average"])
        if status == "open" and 0 < filled < order.amount:
            order.status = OrderStatus.PARTIALLY_FILLED
        fee = raw.get("fee") or {}
        if fee.get("cost") is not None:
            order.fee = float(fee["cost"])
        order.updated_at = utc_now()
