"""Konfigurations-Management.

Lädt die YAML-Konfiguration, validiert sie mit Pydantic und ergänzt
Geheimnisse (API-Keys, Tokens) ausschließlich aus Umgebungsvariablen
bzw. einer ``.env``-Datei. Geheimnisse stehen niemals in der YAML-Datei.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator, model_validator

from tradingbot.core.enums import MarketType, SizingMethod, Timeframe, TradingMode
from tradingbot.core.exceptions import ConfigError

DEFAULT_CONFIG_PATH = Path("config/config.yaml")


class AppConfig(BaseModel):
    """Allgemeine Anwendungseinstellungen."""

    name: str = "TradingBot"
    timezone: str = "UTC"


class TradingConfig(BaseModel):
    """Handels-Grundeinstellungen (Modus, Börse, Symbole, Timeframe)."""

    mode: TradingMode = TradingMode.PAPER
    live_trading_confirmed: bool = False
    exchange: str = "binance"
    market_type: MarketType = MarketType.SPOT
    symbols: list[str] = Field(default_factory=lambda: ["BTC/USDT"])
    timeframe: Timeframe = Timeframe.M5
    candle_history: int = Field(default=300, ge=50, le=5000)
    loop_interval_seconds: float = Field(default=10.0, gt=0)

    @field_validator("symbols")
    @classmethod
    def _symbols_not_empty(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("Mindestens ein Symbol muss konfiguriert sein")
        return value

    @model_validator(mode="after")
    def _enforce_live_confirmation(self) -> "TradingConfig":
        """Sicherheitsnetz: Live-Modus erfordert explizite Bestätigung."""
        if self.mode is TradingMode.LIVE and not self.live_trading_confirmed:
            raise ValueError(
                "Live-Trading erfordert 'trading.live_trading_confirmed: true'. "
                "Ohne explizite Bestätigung startet der Bot nur im Paper-Modus."
            )
        return self


class PaperConfig(BaseModel):
    """Parameter der Paper-Trading-Simulation."""

    initial_balance: float = Field(default=10_000.0, gt=0)
    commission_rate: float = Field(default=0.001, ge=0, lt=0.1)
    slippage_rate: float = Field(default=0.0005, ge=0, lt=0.1)
    spread_rate: float = Field(default=0.0002, ge=0, lt=0.1)


class ExchangeCredentials(BaseModel):
    """API-Zugangsdaten einer Börse (nur aus Umgebungsvariablen befüllt)."""

    api_key: str = ""
    api_secret: str = ""
    password: str = ""

    @property
    def is_configured(self) -> bool:
        """True, wenn Key und Secret vorhanden sind."""
        return bool(self.api_key and self.api_secret)


class ExchangeSettings(BaseModel):
    """Nicht-geheime Einstellungen einer Börse."""

    testnet: bool = False
    rate_limit: bool = True
    options: dict[str, Any] = Field(default_factory=dict)


class StrategyInstanceConfig(BaseModel):
    """Konfiguration einer aktiven Strategie-Instanz."""

    name: str
    symbols: list[str] = Field(default_factory=list)
    timeframe: Timeframe = Timeframe.M5
    params: dict[str, Any] = Field(default_factory=dict)


class StrategiesConfig(BaseModel):
    """Liste der aktiven Strategien."""

    active: list[StrategyInstanceConfig] = Field(default_factory=list)


class LossStreakCooldownConfig(BaseModel):
    """Handelspause nach Verlustserie."""

    max_consecutive_losses: int = Field(default=3, ge=1)
    cooldown_minutes: float = Field(default=60.0, ge=0)


class RiskConfig(BaseModel):
    """Risiko-Management-Parameter."""

    risk_per_trade: float = Field(default=0.01, gt=0, le=0.5)
    max_open_positions: int = Field(default=5, ge=1)
    max_daily_loss: float = Field(default=0.03, gt=0, le=1.0)
    max_drawdown: float = Field(default=0.15, gt=0, le=1.0)
    max_daily_trades: int = Field(default=20, ge=1)
    max_leverage: float = Field(default=3.0, ge=1.0)
    stop_loss: float = Field(default=0.02, ge=0, le=0.9)
    take_profit: float = Field(default=0.04, ge=0)
    trailing_stop: float = Field(default=0.0, ge=0, le=0.9)
    break_even_trigger: float = Field(default=0.0, ge=0)
    loss_streak_cooldown: LossStreakCooldownConfig = Field(
        default_factory=LossStreakCooldownConfig
    )


class SizingConfig(BaseModel):
    """Positionsgrößen-Parameter."""

    method: SizingMethod = SizingMethod.PERCENT_RISK
    fixed_amount: float = Field(default=500.0, gt=0)
    kelly_fraction: float = Field(default=0.5, gt=0, le=1.0)
    kelly_lookback: int = Field(default=30, ge=5)
    atr_period: int = Field(default=14, ge=2)
    atr_risk_multiple: float = Field(default=1.5, gt=0)


class DatabaseConfig(BaseModel):
    """Datenbank-Einstellungen (SQLite-Standard, PostgreSQL optional)."""

    url: str = "sqlite:///storage/tradingbot.db"
    echo: bool = False


class DataConfig(BaseModel):
    """Einstellungen der Datenbeschaffung/-speicherung."""

    storage_dir: Path = Path("storage/historical")
    backtest_dir: Path = Path("storage/backtests")
    download_batch_size: int = Field(default=1000, ge=10, le=1500)


class LoggingConfig(BaseModel):
    """Logging-Einstellungen."""

    level: str = "INFO"
    dir: Path = Path("logs")
    file_name: str = "tradingbot.log"
    max_bytes: int = Field(default=10 * 1024 * 1024, ge=1024)
    backup_count: int = Field(default=10, ge=1)
    console: bool = True
    json_format: bool = False

    @field_validator("level")
    @classmethod
    def _valid_level(cls, value: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = value.upper()
        if upper not in allowed:
            raise ValueError(f"Ungültiges Log-Level '{value}'. Erlaubt: {sorted(allowed)}")
        return upper


class NotificationEventsConfig(BaseModel):
    """Welche Ereignisse Benachrichtigungen auslösen."""

    trade_opened: bool = True
    trade_closed: bool = True
    error: bool = True
    max_drawdown: bool = True
    daily_loss: bool = True


class NotificationsConfig(BaseModel):
    """Benachrichtigungskanäle und -ereignisse."""

    enabled: bool = False
    channels: list[str] = Field(default_factory=list)
    events: NotificationEventsConfig = Field(default_factory=NotificationEventsConfig)

    @field_validator("channels")
    @classmethod
    def _valid_channels(cls, value: list[str]) -> list[str]:
        allowed = {"discord", "telegram", "email"}
        for channel in value:
            if channel not in allowed:
                raise ValueError(f"Unbekannter Kanal '{channel}'. Erlaubt: {sorted(allowed)}")
        return value


class DashboardConfig(BaseModel):
    """Dashboard-Einstellungen."""

    host: str = "0.0.0.0"
    port: int = Field(default=8501, ge=1, le=65535)
    refresh_seconds: float = Field(default=5.0, gt=0)


class BacktestConfig(BaseModel):
    """Standardwerte für Backtests."""

    initial_balance: float = Field(default=10_000.0, gt=0)
    commission_rate: float = Field(default=0.001, ge=0, lt=0.1)
    slippage_rate: float = Field(default=0.0005, ge=0, lt=0.1)
    spread_rate: float = Field(default=0.0002, ge=0, lt=0.1)
    leverage: float = Field(default=1.0, ge=1.0)


class ScannerDetectorSettings(BaseModel):
    """Parameter der Buy-the-Dip-Mustererkennung."""

    min_history: int = Field(default=120, ge=60)
    trend_lookback: int = Field(default=120, ge=40)
    min_trend_gain: float = Field(default=0.10, ge=0.0, le=2.0)
    high_lookback: int = Field(default=60, ge=10)
    min_dip: float = Field(default=0.03, gt=0, lt=0.5)
    max_dip: float = Field(default=0.20, gt=0, lt=0.9)
    min_dip_bars: int = Field(default=2, ge=1)
    max_dip_bars: int = Field(default=30, ge=2)
    panic_atr_mult: float = Field(default=2.5, gt=0)
    volume_spike_limit: float = Field(default=2.25, gt=1.0)
    support_max_distance: float = Field(default=0.04, gt=0, le=0.2)
    support_undercut_tolerance: float = Field(default=0.015, ge=0, le=0.1)
    invalidation_pct: float = Field(default=0.03, gt=0, le=0.2)
    stop_atr_mult: float = Field(default=0.5, ge=0)
    min_trend_score: float = Field(default=0.6, ge=0, le=1.0)
    rs_lookback: int = Field(default=63, ge=20)

    @model_validator(mode="after")
    def _dip_bounds(self) -> "ScannerDetectorSettings":
        if self.min_dip >= self.max_dip:
            raise ValueError("scanner.detector: min_dip muss kleiner als max_dip sein")
        if self.min_dip_bars >= self.max_dip_bars:
            raise ValueError("scanner.detector: min_dip_bars muss kleiner als max_dip_bars sein")
        return self


class ScannerFilters(BaseModel):
    """Vorfilter des Scan-Universums."""

    min_price: float = Field(default=5.0, ge=0)
    max_price: float = Field(default=0.0, ge=0, description="0 = unbegrenzt")
    min_avg_volume: float = Field(default=300_000.0, ge=0)
    min_score: float = Field(default=50.0, ge=0, le=100)


class ScannerNotificationEventsConfig(BaseModel):
    """Welche Scanner-Ereignisse Benachrichtigungen auslösen."""

    new_setup: bool = True
    confirmed: bool = True
    entry_signal: bool = True
    target_reached: bool = True
    invalidated: bool = True
    trade_opened: bool = True
    trade_closed: bool = True


class ScannerNotificationsConfig(BaseModel):
    """Benachrichtigungen des Scanners (unabhängig vom Bot)."""

    enabled: bool = False
    channels: list[str] = Field(default_factory=list)
    events: ScannerNotificationEventsConfig = Field(
        default_factory=ScannerNotificationEventsConfig
    )

    @field_validator("channels")
    @classmethod
    def _valid_channels(cls, value: list[str]) -> list[str]:
        allowed = {"discord", "telegram", "email"}
        for channel in value:
            if channel not in allowed:
                raise ValueError(f"Unbekannter Kanal '{channel}'. Erlaubt: {sorted(allowed)}")
        return value


class ScannerPaperTradingConfig(BaseModel):
    """Paper-Trading-Parameter des Scanners (eigenes, vom Bot unabhängiges Depot).

    Attributes:
        enabled: Ob der Scanner selbst Paper-Trades ausführt.
        initial_balance: Startkapital des Scanner-Depots (Quote-Währung: USD).
        risk_per_trade: Kapitalanteil, der pro Trade riskiert wird (Stop-Distanz-basiert).
        commission_rate: Kommission pro Fill als Bruchteil.
        max_open_positions: Maximal gleichzeitig offene Positionen.
        partial_exit_at_target1: Bei Erreichen von Ziel 1 die Hälfte verkaufen
            und den Stop auf den Einstand ziehen, statt komplett zu schließen.
    """

    enabled: bool = True
    initial_balance: float = Field(default=25_000.0, gt=0)
    risk_per_trade: float = Field(default=0.02, gt=0, le=0.2)
    commission_rate: float = Field(default=0.0005, ge=0, lt=0.05)
    max_open_positions: int = Field(default=10, ge=1)
    partial_exit_at_target1: bool = True


class ScannerConfig(BaseModel):
    """Konfiguration des Buy-the-Dip-Marktscanners."""

    enabled: bool = True
    universes: list[str] = Field(default_factory=lambda: ["sp500", "nasdaq_100"])
    custom_tickers: list[str] = Field(default_factory=list)
    universe_csv: Path | None = None
    interval_seconds: float = Field(default=900.0, ge=60.0)
    history_period: str = "1y"
    batch_size: int = Field(default=100, ge=10, le=500)
    cache_ttl_seconds: float = Field(default=600.0, ge=0)
    benchmark_symbol: str = "SPY"
    dashboard_port: int = Field(default=8502, ge=1, le=65535)
    filters: ScannerFilters = Field(default_factory=ScannerFilters)
    detector: ScannerDetectorSettings = Field(default_factory=ScannerDetectorSettings)
    paper_trading: ScannerPaperTradingConfig = Field(default_factory=ScannerPaperTradingConfig)
    notifications: ScannerNotificationsConfig = Field(
        default_factory=ScannerNotificationsConfig
    )


class Config(BaseModel):
    """Vollständige, validierte Bot-Konfiguration."""

    app: AppConfig = Field(default_factory=AppConfig)
    trading: TradingConfig = Field(default_factory=TradingConfig)
    paper: PaperConfig = Field(default_factory=PaperConfig)
    exchanges: dict[str, ExchangeSettings] = Field(default_factory=dict)
    strategies: StrategiesConfig = Field(default_factory=StrategiesConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    sizing: SizingConfig = Field(default_factory=SizingConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    data: DataConfig = Field(default_factory=DataConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)
    dashboard: DashboardConfig = Field(default_factory=DashboardConfig)
    backtest: BacktestConfig = Field(default_factory=BacktestConfig)
    scanner: ScannerConfig = Field(default_factory=ScannerConfig)

    def exchange_settings(self, exchange: str) -> ExchangeSettings:
        """Einstellungen einer Börse; liefert Defaults für unbekannte Börsen."""
        return self.exchanges.get(exchange, ExchangeSettings())


def load_credentials(exchange: str) -> ExchangeCredentials:
    """Lädt API-Zugangsdaten einer Börse aus Umgebungsvariablen.

    Erwartete Variablen: ``<EXCHANGE>_API_KEY``, ``<EXCHANGE>_API_SECRET``
    und optional ``<EXCHANGE>_API_PASSWORD`` (z. B. für OKX).

    Args:
        exchange: Börsenname, z. B. ``"binance"``.

    Returns:
        Zugangsdaten; Felder sind leer, wenn Variablen nicht gesetzt sind.
    """
    prefix = exchange.upper()
    return ExchangeCredentials(
        api_key=os.environ.get(f"{prefix}_API_KEY", ""),
        api_secret=os.environ.get(f"{prefix}_API_SECRET", ""),
        password=os.environ.get(f"{prefix}_API_PASSWORD", ""),
    )


def load_config(path: str | Path = DEFAULT_CONFIG_PATH, *, env_file: str | Path | None = ".env") -> Config:
    """Lädt und validiert die Bot-Konfiguration.

    Reihenfolge:
        1. ``.env``-Datei laden (falls vorhanden) – nur Geheimnisse/Overrides.
        2. YAML-Datei parsen.
        3. Umgebungsvariablen-Overrides anwenden (z. B. ``TRADINGBOT_DB_URL``).
        4. Pydantic-Validierung.

    Args:
        path: Pfad zur YAML-Konfigurationsdatei.
        env_file: Pfad zur ``.env``-Datei oder None, um das Laden zu überspringen.

    Returns:
        Validierte :class:`Config`.

    Raises:
        ConfigError: Bei fehlender Datei, ungültigem YAML oder Validierungsfehlern.
    """
    if env_file is not None:
        load_dotenv(env_file, override=False)

    config_path = Path(path)
    if not config_path.exists():
        raise ConfigError(f"Konfigurationsdatei nicht gefunden: {config_path.resolve()}")

    try:
        raw: dict[str, Any] = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Ungültiges YAML in {config_path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"Konfiguration muss ein Mapping sein, erhalten: {type(raw).__name__}")

    db_url_override = os.environ.get("TRADINGBOT_DB_URL")
    if db_url_override:
        raw.setdefault("database", {})["url"] = db_url_override

    try:
        return Config.model_validate(raw)
    except Exception as exc:  # pydantic.ValidationError und Folgefehler
        raise ConfigError(f"Ungültige Konfiguration: {exc}") from exc
