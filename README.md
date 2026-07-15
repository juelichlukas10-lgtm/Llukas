# TradingBot – Professioneller algorithmischer Trading-Bot

Modularer, vollständig asynchroner Trading-Bot für Kryptobörsen mit
Paper-Trading, Live-Trading, Backtesting, Parameter-Optimierung und
Streamlit-Dashboard.

> ⚠️ **Sicherheitsprinzip:** Der Bot startet **immer im Paper-Modus**.
> Live-Trading erfordert die explizite Doppel-Bestätigung in der
> Konfiguration (`mode: live` **und** `live_trading_confirmed: true`).

## Features

- **Börsen:** Binance, Bybit, OKX, Kraken (und alle weiteren CCXT-Pro-Börsen)
  über eine einheitliche Abstraktion; neue Börsen per `register_exchange()`
  ohne Änderung am Kernsystem
- **Live-Daten:** REST + WebSocket (Ticker, Kerzen, Orderbuch, Trades,
  Funding-Rate, Open Interest) mit automatischem Reconnect
- **Historische Daten:** automatischer, inkrementeller Download in lokale
  Parquet-Dateien; 8 Timeframes (1m–1d) mit Multi-Timeframe-Resampling
- **18 Strategien** als Plugins (eine Datei = eine Strategie): EMA Crossover,
  RSI, MACD, Bollinger, VWAP, Supertrend, Donchian, ATR Breakout,
  Mean Reversion, Momentum, Trend Following, Breakout, Scalping, Grid, DCA,
  Multi-Timeframe-Confirmation, Volume, Order Flow
- **19 Indikatoren** in reinem pandas/numpy (EMA, SMA, RSI, MACD, ADX, ATR,
  Stochastic, CCI, VWAP, OBV, MFI, Bollinger, Ichimoku, Keltner,
  Pivot Points, Parabolic SAR, Donchian, Supertrend, Volume Profile)
- **Risiko-Management:** SL/TP, Trailing-Stop, Break-Even, Max-Drawdown- und
  Tagesverlust-Stopp, Positions- und Trade-Limits, Cooldown nach
  Verlustserien, Hebel-Kontrolle
- **Positionsgrößen:** fix, %-Risiko, Kelly-Kriterium, ATR-basiert
- **Ordertypen:** Market, Limit, Stop-Market, Stop-Limit, Trailing-Stop,
  Reduce-Only, Post-Only – inkl. Retry, Timeout-Stornierung und Teilverkäufen
- **Backtesting:** Event-basiert, Multi-Asset, Kommission/Slippage/Spread/
  Hebel, intra-bar SL/TP; Grid Search, Random Search, Walk-Forward-Analyse
- **Kennzahlen:** PnL, Trefferquote, Profit Factor, Sharpe, Sortino, Calmar,
  Max Drawdown, Expectancy, Equity-/Drawdown-Kurve u. v. m.
- **Persistenz:** SQLite (Standard) oder PostgreSQL via SQLAlchemy
- **Dashboard:** Streamlit mit Live-Kennzahlen, Candlestick-Charts,
  Equity-Kurve, PnL-Heatmap, Positionen, Backtests und Logs
- **Benachrichtigungen:** Discord, Telegram, E-Mail
- **Buy-the-Dip-Aktienscanner:** eigenständiges Modul mit eigenem Dashboard –
  überwacht permanent hunderte Aktien (S&P 500, Nasdaq 100, Dow, EU) und
  erkennt Rücksetzer in intakten Aufwärtstrends mit Score 0–100
  (siehe [docs/scanner.md](docs/scanner.md))
- **233 Tests** (Unit + Integration), strukturiertes Logging mit Rotation

## Schnellstart

```bash
# 1. Abhängigkeiten installieren (Python >= 3.12)
pip install -r requirements.txt

# 2. Umgebungsvariablen anlegen (API-Keys NIEMALS in den Code!)
cp .env.example .env       # Windows: copy .env.example .env

# 3. Konfiguration prüfen/anpassen
#    -> config/config.yaml (Standard: Paper-Trading auf Binance)

# 4. Bot starten (Paper-Modus)
python main.py run

# 5. Dashboard öffnen (separates Terminal)
python main.py dashboard
```

## Windows: Als Hintergrund-App mit Autostart (ohne Terminal)

Statt den Bot manuell über `python main.py run` zu starten, lässt er sich
als System-Tray-App einrichten, die automatisch bei der Windows-Anmeldung
startet – ganz ohne sichtbares Terminal-Fenster:

```powershell
pip install -r requirements-desktop.txt
powershell -ExecutionPolicy Bypass -File scripts\install_autostart.ps1
```

Danach erscheint ein Icon in der Taskleiste (unten rechts), über das sich
Bot und Dashboard starten/stoppen lassen (Rechtsklick fürs Menü,
Doppelklick öffnet direkt das Dashboard im Browser). Ab dem nächsten
Windows-Login startet der Bot automatisch von selbst.

Entfernen: `powershell -File scripts\uninstall_autostart.ps1`
(beendet keine bereits laufende Instanz – dafür im Tray-Menü „Beenden“ wählen).

## CLI-Befehle

```bash
python main.py run                  # Bot starten (Paper-Modus als Standard)
python main.py list-strategies     # Alle 18 Strategien anzeigen
python main.py dashboard           # Streamlit-Dashboard starten
python main.py scan                # Buy-the-Dip-Aktienscanner starten
python main.py scanner-dashboard  # Scanner-Dashboard (Port 8502)

# Historische Daten laden
python main.py download --symbol BTC/USDT --timeframe 1h --start 2023-01-01

# Backtest
python main.py backtest --strategy ema_crossover --symbol BTC/USDT \
    --timeframe 1h --start 2023-01-01 --end 2024-01-01 \
    --params '{"fast_period": 9, "slow_period": 21}' --download

# Parameter-Optimierung (Grid Search)
python main.py optimize --strategy ema_crossover --symbol BTC/USDT \
    --timeframe 1h --start 2023-01-01 \
    --grid '{"fast_period": [6, 9, 12], "slow_period": [21, 34]}' \
    --metric sharpe_ratio

# Walk-Forward-Analyse
python main.py walkforward --strategy ema_crossover --symbol BTC/USDT \
    --timeframe 1h --start 2022-01-01 \
    --grid '{"fast_period": [6, 9, 12], "slow_period": [21, 34]}' --windows 4
```

## Projektstruktur

```
tradingbot/
├── core/          Konfiguration, Logging, Modelle, Event-Bus, Engine
├── exchange/      Exchange-Abstraktion (CCXT, Paper-Simulator, Factory)
├── data/          Downloader, Parquet-Storage, Resampler, Live-Streams
├── database/      SQLAlchemy-Modelle und Repository (SQLite/PostgreSQL)
├── strategies/    Plugin-System + 18 Strategien (1 Datei = 1 Strategie)
├── risk/          RiskManager und Positionsgrößen (fix/%/Kelly/ATR)
├── execution/     OrderManager und ExecutionEngine
├── backtesting/   Backtest-Engine und Optimierer (Grid/Random/Walk-Forward)
├── analytics/     Indikatoren und Performance-Kennzahlen
├── monitoring/    Benachrichtigungen (Discord/Telegram/E-Mail)
├── scanner/       Buy-the-Dip-Aktienscanner (eigenständiges Modul)
└── dashboard/     Streamlit-Apps (Bot-Dashboard + Scanner-Dashboard)
config/            YAML-Konfiguration
tests/             Unit- und Integrationstests (pytest)
docs/              Ausführliche Dokumentation
docker/            Dockerfile
storage/           Historische Daten, Backtests, SQLite-DB
logs/              Rotierende Logdateien
```

## Eigene Strategie hinzufügen

Eine Datei in `tradingbot/strategies/` ablegen – fertig:

```python
# tradingbot/strategies/my_strategy.py
from tradingbot.core.enums import SignalAction
from tradingbot.strategies.base import Strategy, StrategyContext
from tradingbot.strategies.registry import register_strategy

@register_strategy("my_strategy")
class MyStrategy(Strategy):
    """Kauft, wenn die letzte Kerze grün ist (Demo)."""

    default_params = {"lookback_period": 20}

    def generate_signal(self, df, symbol, context):
        if not self.has_enough_history(df):
            return None
        if context.position_side(symbol) is None and df["close"].iloc[-1] > df["open"].iloc[-1]:
            return self.make_signal(SignalAction.BUY, symbol, df, reason="Grüne Kerze")
        return None
```

Aktivierung in `config/config.yaml` unter `strategies.active`.

## Tests

```bash
python -m pytest tests/          # alle 210 Tests
python -m pytest tests/unit      # nur Unit-Tests
python -m pytest --cov=tradingbot tests/   # mit Coverage
```

## Docker

```bash
docker compose up -d bot          # Bot (Paper-Modus)
docker compose up -d dashboard    # Dashboard auf Port 8501
docker compose --profile postgres up -d   # optional mit PostgreSQL
```

## Dokumentation

| Dokument | Inhalt |
| --- | --- |
| [docs/installation.md](docs/installation.md) | Installation (Windows/Linux/macOS) |
| [docs/configuration.md](docs/configuration.md) | Alle Konfigurationsoptionen |
| [docs/strategies.md](docs/strategies.md) | Strategien und Plugin-System |
| [docs/backtesting.md](docs/backtesting.md) | Backtesting und Optimierung |
| [docs/scanner.md](docs/scanner.md) | Buy-the-Dip-Aktienscanner |
| [docs/deployment.md](docs/deployment.md) | Docker, Server-Betrieb |
| [docs/faq.md](docs/faq.md) | Häufige Fragen |

## Haftungsausschluss

Diese Software dient ausschließlich zu Bildungs- und Forschungszwecken.
Der Handel mit Kryptowährungen birgt erhebliche Verlustrisiken. Die
Nutzung – insbesondere im Live-Modus – erfolgt auf eigene Verantwortung.
