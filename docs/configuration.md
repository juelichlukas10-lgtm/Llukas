# Konfiguration

Die gesamte Konfiguration erfolgt über **`config/config.yaml`**
(Parameter) und **`.env`** (Geheimnisse). API-Keys stehen niemals in der
YAML-Datei oder im Code.

## Umgebungsvariablen (`.env`)

| Variable | Zweck |
| --- | --- |
| `BINANCE_API_KEY` / `BINANCE_API_SECRET` | Binance-Zugang (nur Live) |
| `BYBIT_API_KEY` / `BYBIT_API_SECRET` | Bybit-Zugang |
| `OKX_API_KEY` / `OKX_API_SECRET` / `OKX_API_PASSWORD` | OKX-Zugang |
| `KRAKEN_API_KEY` / `KRAKEN_API_SECRET` | Kraken-Zugang |
| `TRADINGBOT_DB_URL` | Überschreibt die Datenbank-URL (z. B. PostgreSQL) |
| `DISCORD_WEBHOOK_URL` | Discord-Benachrichtigungen |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | Telegram-Benachrichtigungen |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_FROM`, `EMAIL_TO` | E-Mail-Versand |

## Abschnitte der `config.yaml`

### `trading` – Grundeinstellungen

```yaml
trading:
  mode: "paper"                 # paper | live
  live_trading_confirmed: false # Sicherheitsschalter für Live-Trading
  exchange: "binance"           # binance | bybit | okx | kraken | ...
  market_type: "spot"           # spot | futures
  symbols: ["BTC/USDT", "ETH/USDT"]
  timeframe: "5m"               # 1m 3m 5m 15m 30m 1h 4h 1d
  candle_history: 300           # Kerzen-Kontext für Strategien
  loop_interval_seconds: 10     # Wartungs-Loop-Intervall
```

### Live-Trading aktivieren

Beide Schalter sind erforderlich – ohne Bestätigung verweigert der Bot
den Start im Live-Modus:

```yaml
trading:
  mode: "live"
  live_trading_confirmed: true
```

Zusätzlich müssen API-Keys der Börse in der `.env` hinterlegt sein.

### `paper` – Paper-Trading-Simulation

```yaml
paper:
  initial_balance: 10000.0   # Startkapital (Quote-Währung)
  commission_rate: 0.001     # 0.1 % Kommission je Fill
  slippage_rate: 0.0005      # Slippage auf Market-Orders
  spread_rate: 0.0002        # künstlicher Spread
```

### `strategies` – Aktive Strategien

```yaml
strategies:
  active:
    - name: "ema_crossover"       # Registry-Name (list-strategies)
      symbols: ["BTC/USDT"]       # leer = trading.symbols
      timeframe: "5m"
      params:                     # überschreibt default_params
        fast_period: 12
        slow_period: 26
```

### `risk` – Risiko-Management

| Parameter | Bedeutung |
| --- | --- |
| `risk_per_trade` | Kapitalanteil, der pro Trade riskiert wird (0.01 = 1 %) |
| `max_open_positions` | Maximal gleichzeitig offene Positionen |
| `max_daily_loss` | Tagesverlust-Stopp (0.03 = 3 %) |
| `max_drawdown` | Drawdown-Stopp vom Equity-Hoch (0.15 = 15 %) |
| `max_daily_trades` | Tägliches Trade-Limit |
| `max_leverage` | Hebel-Obergrenze |
| `stop_loss` / `take_profit` | Default-Stops, falls die Strategie keine liefert |
| `trailing_stop` | Trailing-Abstand (0 = aus) |
| `break_even_trigger` | Gewinn, ab dem der SL auf Einstand zieht (0 = aus) |
| `loss_streak_cooldown` | Handelspause nach N Verlusten in Folge |

### `sizing` – Positionsgrößen

```yaml
sizing:
  method: "percent_risk"   # fixed | percent_risk | kelly | atr
  fixed_amount: 500.0      # bei fixed: Quote-Betrag je Trade
  kelly_fraction: 0.5      # Dämpfung des Kelly-Anteils
  kelly_lookback: 30       # Trades für die Kelly-Schätzung
  atr_period: 14
  atr_risk_multiple: 1.5   # Stop-Distanz = 1.5 × ATR
```

### `database` – Persistenz

```yaml
database:
  url: "sqlite:///storage/tradingbot.db"
  # PostgreSQL:
  # url: "postgresql+psycopg2://user:pass@localhost:5432/tradingbot"
  echo: false
```

Die URL kann per `TRADINGBOT_DB_URL` überschrieben werden (empfohlen für
PostgreSQL-Zugangsdaten).

### `logging`

```yaml
logging:
  level: "INFO"          # DEBUG | INFO | WARNING | ERROR
  dir: "logs"
  file_name: "tradingbot.log"
  max_bytes: 10485760    # 10 MB, dann Rotation
  backup_count: 10
  console: true
  json_format: false     # true = strukturierte JSON-Logs
```

### `notifications`

```yaml
notifications:
  enabled: true
  channels: ["discord", "telegram"]   # discord | telegram | email
  events:
    trade_opened: true
    trade_closed: true
    error: true
    max_drawdown: true
    daily_loss: true
```

### `backtest` – Standardwerte für Backtests

```yaml
backtest:
  initial_balance: 10000.0
  commission_rate: 0.001
  slippage_rate: 0.0005
  spread_rate: 0.0002
  leverage: 1.0
```

## Validierung

Die Konfiguration wird beim Start mit Pydantic validiert. Ungültige
Werte (z. B. `mode: live` ohne Bestätigung, unbekannte Timeframes,
negative Limits) führen zu einer klaren Fehlermeldung und der Bot
startet nicht.
