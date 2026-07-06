# Strategien

## Mitgelieferte Strategien (18)

| Name | Typ | Kernidee | Wichtigste Parameter |
| --- | --- | --- | --- |
| `ema_crossover` | Trendfolge | Golden/Death Cross zweier EMAs | `fast_period`, `slow_period` |
| `rsi` | Mean Reversion | Drehung aus überkauft/überverkauft | `period`, `oversold`, `overbought` |
| `macd` | Momentum | MACD-/Signallinien-Kreuz | `fast_period`, `slow_period`, `signal_period` |
| `bollinger` | Mean Reversion | Wiedereintritt ins Band | `bb_period`, `std_dev`, `exit_at_middle` |
| `vwap` | Mean Reversion | Rückkehr zum Intraday-VWAP | `deviation`, `exit_deviation` |
| `supertrend` | Trendfolge | Richtungswechsel des Supertrends | `st_period`, `multiplier` |
| `donchian` | Breakout | Turtle-Kanalausbruch | `entry_period`, `exit_period` |
| `atr_breakout` | Breakout | Impuls > k × ATR | `atr_period`, `multiplier` |
| `mean_reversion` | Mean Reversion | Z-Score-Extreme | `lookback_period`, `entry_z`, `exit_z` |
| `momentum` | Momentum | Rate-of-Change-Schwelle | `roc_period`, `entry_threshold` |
| `trend_following` | Trendfolge | EMA-Richtung + ADX-Filter | `trend_period`, `adx_threshold` |
| `breakout` | Breakout | Range-Ausbruch + Volumenfilter | `lookback_period`, `volume_factor` |
| `scalping` | Scalping | EMA-Trend + Stochastic-Trigger | `ema_period`, `target_pct`, `stop_pct` |
| `grid` | Market Making | Preisraster um Anker | `grid_spacing`, `grid_levels` |
| `dca` | Akkumulation | Intervallkäufe + Dip-Bonus | `interval_candles`, `dip_threshold` |
| `mtf_confirmation` | Multi-Timeframe | HTF-Trend + LTF-Rücksetzer | `higher_timeframe`, `trend_period` |
| `volume` | Volumen | Volume-Spike + OBV-Filter | `spike_factor`, `volume_period` |
| `order_flow` | Order Flow | Orderbuch-Imbalance (Fallback: Kerzen-Delta) | `imbalance_threshold`, `depth` |

Alle Long-orientierten Strategien unterstützen den Parameter
`allow_short: true` für symmetrische Short-Logik (nur Futures sinnvoll).

Details und Standardwerte: `python main.py list-strategies`

## Architektur des Plugin-Systems

- Jede Datei in `tradingbot/strategies/` (außer `base.py`, `registry.py`)
  ist ein Plugin und wird beim Start automatisch importiert.
- Registrierung per Decorator: `@register_strategy("name")`.
- Strategien sind **reine Signalgeber** – sie platzieren keine Orders.
  Risiko-Prüfung, Positionsgröße und Ausführung übernehmen RiskManager,
  PositionSizer und ExecutionEngine.
- Dieselbe Strategie-Instanz läuft unverändert im Live-Betrieb **und**
  im Backtest.

## Eigene Strategie schreiben

```python
# tradingbot/strategies/rsi_divergence.py
"""RSI-Divergenz-Strategie (Beispiel für ein eigenes Plugin)."""

import pandas as pd

from tradingbot.analytics.indicators import rsi
from tradingbot.core.enums import PositionSide, SignalAction
from tradingbot.core.models import Signal
from tradingbot.strategies.base import Strategy, StrategyContext
from tradingbot.strategies.registry import register_strategy


@register_strategy("rsi_divergence")
class RsiDivergenceStrategy(Strategy):
    """Kauft bullische RSI-Divergenzen (tieferes Preistief, höheres RSI-Tief)."""

    default_params = {"rsi_period": 14, "swing_period": 5}

    def generate_signal(
        self, df: pd.DataFrame, symbol: str, context: StrategyContext
    ) -> Signal | None:
        if not self.has_enough_history(df):
            return None
        values = rsi(df["close"], self.params["rsi_period"])
        window = self.params["swing_period"]

        price_low_now = df["low"].tail(window).min()
        price_low_prev = df["low"].iloc[-3 * window : -window].min()
        rsi_low_now = values.tail(window).min()
        rsi_low_prev = values.iloc[-3 * window : -window].min()

        bullish_divergence = price_low_now < price_low_prev and rsi_low_now > rsi_low_prev
        if bullish_divergence and context.position_side(symbol) is None:
            return self.make_signal(
                SignalAction.BUY, symbol, df,
                reason="Bullische RSI-Divergenz",
                stop_loss=float(price_low_now) * 0.995,
            )
        if context.position_side(symbol) is PositionSide.LONG and values.iloc[-1] > 70:
            return self.make_signal(SignalAction.CLOSE_LONG, symbol, df, reason="RSI überkauft")
        return None
```

### Verfügbare Bausteine

| Baustein | Zweck |
| --- | --- |
| `self.params` | Gemergte Parameter (Defaults + Konfiguration) |
| `self.has_enough_history(df)` | Genug Kerzen für die Auswertung? |
| `self.make_signal(...)` | Signal mit Preis/Zeit der letzten Kerze |
| `context.position_side(symbol)` | Aktuelle Position (LONG/SHORT/None) |
| `context.get_candles(symbol, tf)` | Daten anderer Timeframes (MTF) |
| `context.get_order_book(symbol)` | Orderbuch (falls verfügbar) |
| `tradingbot.analytics.indicators` | Alle 19 Indikatoren |

### Signal-Konventionen

| Aktion | Bedeutung |
| --- | --- |
| `SignalAction.BUY` | Long eröffnen (nur wenn flach) |
| `SignalAction.SELL` | Short eröffnen (nur wenn flach, Futures) |
| `SignalAction.CLOSE_LONG` / `CLOSE_SHORT` | Position schließen |
| `None` zurückgeben | Kein Handlungsbedarf |

Optionale Signal-Felder: `stop_loss`, `take_profit` (überschreiben die
Risiko-Defaults), `confidence` (0–1), `reason` (Logging), `metadata`.

### Aktivieren und Testen

```yaml
# config/config.yaml
strategies:
  active:
    - name: "rsi_divergence"
      symbols: ["BTC/USDT"]
      timeframe: "1h"
      params:
        rsi_period: 14
```

```bash
python main.py list-strategies                     # Plugin gefunden?
python main.py backtest --strategy rsi_divergence \
    --symbol BTC/USDT --timeframe 1h --start 2024-01-01 --download
```

### Parameter-Validierung

`_validate_params()` überschreiben, um ungültige Konfigurationen früh
abzulehnen:

```python
def _validate_params(self) -> None:
    super()._validate_params()
    if self.params["swing_period"] < 2:
        raise StrategyError(f"{self.name}: swing_period muss >= 2 sein")
```

### Multi-Timeframe-Strategien

`additional_timeframes` deklarieren, damit die Engine die zusätzlichen
Streams abonniert (siehe `mtf_confirmation.py` als Referenz):

```python
@property
def additional_timeframes(self) -> list[Timeframe]:
    return [Timeframe.H4]
```
