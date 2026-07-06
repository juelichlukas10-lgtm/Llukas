# Backtesting und Optimierung

## Ablauf

1. **Daten laden** – einmalig oder inkrementell (setzt lokale Daten fort):

   ```bash
   python main.py download --symbol BTC/USDT --symbol ETH/USDT \
       --timeframe 1h --timeframe 4h --start 2022-01-01
   ```

   Die Daten liegen als Parquet unter
   `storage/historical/<exchange>/<symbol>/<timeframe>.parquet`.

2. **Backtest ausführen** (`--download` lädt fehlende Daten automatisch):

   ```bash
   python main.py backtest --strategy supertrend --symbol BTC/USDT \
       --timeframe 1h --start 2022-01-01 --end 2024-01-01 \
       --params '{"st_period": 10, "multiplier": 3.0}'
   ```

   Ergebnis: Kennzahlen-Bericht im Terminal, Persistenz in der Datenbank
   (Tab „Strategien & Backtests" im Dashboard) und Equity-Kurve als CSV
   unter `storage/backtests/`.

## Simulationsmodell

- **Event-basiert:** Kerzen werden chronologisch verarbeitet; Strategien
  sehen ausschließlich Daten bis zur aktuellen Kerze (kein Look-Ahead).
- **Kosten:** Kommission, Slippage und halber Spread auf jeden Fill.
- **Stops intra-bar:** SL/TP werden gegen High/Low der Kerze geprüft;
  Gaps über den Stop hinaus füllen zum schlechteren realistischen Preis.
- **Trailing-Stop & Break-Even** wie im Live-Betrieb.
- **Hebel:** Margin-Modell (`backtest.leverage`), Positionsgrößen über den
  konfigurierten `PositionSizer`.
- **Multi-Asset:** mehrere Symbole gleichzeitig auf gemeinsamer Zeitachse.
- **Risiko-Limits:** Max-Drawdown- und Tagesverlust-Stopp blockieren neue
  Einstiege; offene Positionen werden weiter verwaltet.
- **Warmup:** Die ersten `required_history`-Kerzen erzeugen keine Signale.

## Kennzahlen

`total_pnl`, `total_return`, `gross_profit/loss`, `trade_count`,
`win_rate`, `profit_factor`, `average_win/loss`, `risk_reward_ratio`,
`expectancy`, `sharpe_ratio`, `sortino_ratio`, `calmar_ratio`,
`max_drawdown`, `total_fees` – plus Equity- und Drawdown-Kurve.

Sharpe/Sortino werden aus den periodischen Equity-Renditen annualisiert
(Periodenlänge wird aus dem Zeitindex abgeleitet).

## Parameter-Optimierung

### Grid Search (erschöpfend)

```bash
python main.py optimize --strategy ema_crossover --symbol BTC/USDT \
    --timeframe 1h --start 2022-01-01 \
    --grid '{"fast_period": [6, 9, 12, 16], "slow_period": [21, 34, 55]}' \
    --metric sharpe_ratio
```

### Random Search (Stichprobe)

2er-Listen aus Zahlen werden als `(min, max)`-Bereich interpretiert:

```bash
python main.py optimize --method random --iterations 100 \
    --strategy ema_crossover --symbol BTC/USDT --timeframe 1h \
    --start 2022-01-01 \
    --grid '{"fast_period": [3, 20], "slow_period": [21, 34, 55]}'
```

### Zielmetriken

Alle Bericht-Kennzahlen sind wählbar, z. B. `sharpe_ratio` (Standard),
`total_pnl`, `profit_factor`, `expectancy`, `calmar_ratio` oder
`max_drawdown` (wird minimiert). Kombinationen mit weniger als
`min_trades` Trades werden als unbrauchbar gewertet.

## Walk-Forward-Analyse

Schützt vor Overfitting: Die Daten werden in Fenster geteilt, je Fenster
wird auf dem Trainingsteil optimiert und auf dem anschließenden,
ungesehenen Testteil validiert.

```bash
python main.py walkforward --strategy ema_crossover --symbol BTC/USDT \
    --timeframe 1h --start 2021-01-01 \
    --grid '{"fast_period": [6, 9, 12], "slow_period": [21, 34]}' \
    --windows 4 --train-ratio 0.75
```

Der Bericht zeigt die besten Parameter je Fenster sowie die
zusammengesetzte **Out-of-Sample-Equity** mit allen Kennzahlen — die
realistischste Schätzung der Live-Erwartung.

## Programmatische Nutzung

```python
from tradingbot.backtesting import BacktestEngine, BacktestSettings
from tradingbot.core.config import RiskConfig, SizingConfig
from tradingbot.core.enums import Timeframe
from tradingbot.data.storage import CandleStorage
from tradingbot.strategies import create_strategy

storage = CandleStorage("storage/historical")
data = {"BTC/USDT": storage.load("binance", "BTC/USDT", Timeframe.H1)}

engine = BacktestEngine(
    BacktestSettings(initial_balance=10_000, commission_rate=0.001),
    RiskConfig(stop_loss=0.03, take_profit=0.06),
    SizingConfig(),
)
strategy = create_strategy("supertrend", ["BTC/USDT"], Timeframe.H1)
result = engine.run(strategy, data)

print(result.report.to_dict())
result.equity_curve.plot()
```
