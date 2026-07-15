# Buy-the-Dip-Marktscanner

Eigenständiges Modul, das unabhängig vom Trading-Bot läuft und ein
großes Aktienuniversum permanent nach **Buy-the-Dip-Setups** durchsucht:
Aktien in intaktem Aufwärtstrend, die geordnet in Richtung einer
relevanten Unterstützung korrigieren und erste Stabilisierungsanzeichen
zeigen. Andere Strategien analysiert der Scanner bewusst nicht.

## Schnellstart

```bash
# Scanner permanent laufen lassen (Zyklus gemäß Konfiguration)
python main.py scan

# Nur ein Durchlauf (z. B. zum Testen)
python main.py scan --once

# Eigenes Dashboard (Standard: Port 8502)
python main.py scanner-dashboard
```

Alternativ über das Tray-Icon: „Scanner starten/stoppen" und
„Scanner-Dashboard öffnen".

## Setup-Definition (objektive Regeln)

Ein Symbol wird nur dann zum Setup, wenn **alle** Stufen bestehen:

1. **Trendfilter** (`min_trend_score`, Standard 0.6): Kurs über EMA100/
   EMA200, EMA50 über EMA200, positiver EMA50-Slope, Anstieg über das
   Trendfenster ≥ `min_trend_gain`, höhere Tiefs (jüngere Fensterhälfte
   über der älteren).
2. **Rücksetzer**: Abstand vom jüngsten Hoch zwischen `min_dip` (3 %)
   und `max_dip` (20 %), Alter des Hochs zwischen `min_dip_bars` und
   `max_dip_bars`. *Geordnet* heißt: kein Tagesverlust über
   `panic_atr_mult` × ATR und kein Abwärtstag mit mehr als
   `volume_spike_limit` × Durchschnittsvolumen.
3. **Unterstützung**: Kurs höchstens `support_max_distance` (4 %) über
   bzw. `support_undercut_tolerance` (1.5 %) unter einer der Kandidaten:
   EMA20/50/100/200, früheres Ausbruchsniveau, Fibonacci-Retracement
   (38.2/50/61.8 % der Aufwärtsbewegung). Die nächstgelegene gewinnt.
4. **Stabilisierung** (bestimmt den Status): bullische Kerzenformationen
   (Hammer, Bullish Engulfing, starker Schluss), RSI-Drehung aus dem
   Bereich ≤ 50, anziehendes Kaufvolumen.

### Status-Lebenszyklus

| Status | Bedeutung |
| --- | --- |
| 👀 `watching` | Rücksetzer läuft, Kurs nahe Unterstützung, noch keine Stabilisierung |
| ✅ `confirmed` | Mindestens zwei von drei Stabilisierungssignalen |
| 🚀 `entry` | Kurs dreht nach oben, überwindet das Mikro-Hoch und die EMA20 |
| 🎯 `target_reached` | Kursziel 1 (Bezugshoch) nach einem Einstiegssignal erreicht |
| ❌ `invalidated` | Unterstützung > `invalidation_pct` gebrochen oder Trendkriterien verletzt |

### Level je Setup

* **Einstieg**: knapp über dem Hoch der letzten 3 Kerzen (Mikro-Breakout)
* **Stop-Loss**: unter Unterstützung/Rücksetzer-Tief minus `stop_atr_mult` × ATR
* **Ziel 1**: das Bezugshoch des Rücksetzers
* **Ziel 2**: Bezugshoch + Höhe des Rücksetzers (Extension)
* **CRV**: (Ziel 1 − Einstieg) / (Einstieg − Stop)

## Score (0–100)

| Komponente | Max. Punkte | Bewertet |
| --- | --- | --- |
| Trend | 25 | Trendqualität aus dem Trendfilter |
| Rücksetzer | 15 | Ideale Tiefe (~7 %), geordneter Verlauf |
| Unterstützung | 15 | Nähe zur Unterstützung |
| Volumen | 10 | Nachlassender Verkaufsdruck (Down-/Up-Volumen) |
| Stabilisierung | 10 | Kerzen-, RSI- und Volumensignale |
| Relative Stärke | 10 | Outperformance vs. Benchmark (Standard: SPY, 63 Tage) |
| Chance/Risiko | 10 | CRV auf Ziel 1 |
| Status-Bonus | 5 | `confirmed` +3, `entry` +5 |

Nur Setups ab `filters.min_score` (Standard 50) werden gespeichert und
angezeigt; die Rangliste sortiert absteigend nach Score.

## Marktuniversum

Eingebaute Universen (kombinierbar): `sp500`, `nasdaq_100`, `dow_jones`,
`eu_large`, `international`. Erweiterung jederzeit über:

```yaml
scanner:
  universes: ["sp500", "nasdaq_100", "dow_jones", "eu_large"]
  custom_tickers: ["PLTR", "COIN"]
  universe_csv: "config/russell2000.csv"   # CSV mit Spalte: symbol[,name]
```

Delistete oder umbenannte Ticker werden automatisch übersprungen.
Datenquelle ist Yahoo Finance (`yfinance`, keine API-Keys nötig);
weitere Anbieter lassen sich über die ABC `StockDataProvider` ergänzen.

## Performance

* Batch-Downloads (viele Ticker pro Request) in Threads – die
  asyncio-Loop und damit die Hauptanwendung blockieren nie
* TTL-Cache je Symbol (`cache_ttl_seconds`)
* Mustererkennung chunk-weise im Thread-Pool
* Automatischer Retry fehlgeschlagener Ticker mit Backoff
* Gemessen: ~550 Symbole in ~70 s je Zyklus (Standard-Zyklus: 15 min)

## Benachrichtigungen

Discord/Telegram/E-Mail (Zugangsdaten wie beim Bot über die `.env`):

```yaml
scanner:
  notifications:
    enabled: true
    channels: ["telegram"]
    events:
      new_setup: true        # neues Setup erkannt
      confirmed: true        # Stabilisierung bestätigt
      entry_signal: true     # Einstiegssignal ausgelöst
      target_reached: true   # Kursziel 1 erreicht
      invalidated: true      # Setup ungültig geworden
```

## Paper-Trading (eigenes Depot)

Der Scanner kann optional selbst automatisiert Paper-Trades ausführen –
vollständig getrennt vom Kapital/Positionen des Trading-Bots (eigene
Datenbanktabellen `scanner_portfolio`, `scanner_positions`,
`scanner_trades`, eigenes Startkapital).

```yaml
scanner:
  paper_trading:
    enabled: true
    initial_balance: 25000.0
    risk_per_trade: 0.02       # 2% Kapitalrisiko je Trade (Stop-Distanz-basiert)
    commission_rate: 0.0005
    max_open_positions: 10
    partial_exit_at_target1: true
```

**Logik je Zyklus** (Exits vor Einstiegen):

1. **Einstieg**: Sobald ein Setup den Status `entry` erreicht und noch
   keine Position im Symbol besteht (Positionslimit und Kapital
   vorausgesetzt). Positionsgröße = (`risk_per_trade` × Depot-Equity) /
   (Einstieg − Stop) – wie beim Bot-Sizing.
2. **Teilverkauf bei Ziel 1** (falls `partial_exit_at_target1: true`):
   verkauft die Hälfte, zieht den Stop auf den Einstand.
3. **Exit bei Ziel 2**: schließt die Restposition.
4. **Exit bei Stop-Loss**: schließt die (Rest-)Position sofort.
5. **Sofort-Exit bei Ungültigwerden**: wenn ein Setup als `invalidated`
   markiert wird, wird eine offene Position umgehend zum aktuellen
   Kurs geschlossen – unabhängig von Stop/Ziel.

Mehrere Kandidaten mit `entry`-Status werden nach Score priorisiert;
das Positionslimit (`max_open_positions`) begrenzt gleichzeitige
Positionen.

## Dashboard

`python main.py scanner-dashboard` (Port 8502) zeigt:

* Kennzahlen: überwachte Aktien, aktive Setups, Einstiegssignale,
  bester Score, Zeitpunkt/Status des letzten Scans
* Live-Rangliste (Auto-Refresh) mit Symbol, Name, Status, Score,
  Kurs, Tagesveränderung, Abstand zum Hoch, Unterstützung, Trendstärke,
  RSI, Volumen-Ratio, CRV und Erkennungszeitpunkt
* Detail-Chart je Setup: Candlesticks, EMA20/50/200, Unterstützungs-,
  Einstiegs-, Stop- und Ziellinien, Bezugshoch, gefärbtes Volumen
* Score-Aufschlüsselung nach Komponenten
* Eigener Tab „Paper-Trading": Depot-Equity, Bargeld, offene
  Positionen, Trade-Historie mit PnL

Das Dashboard liest ausschließlich aus der Datenbank – Scanner und
Dashboard laufen als getrennte Prozesse und können unabhängig
voneinander gestartet und gestoppt werden.

## Alle Parameter

Siehe `scanner:`-Abschnitt in `config/config.yaml` – jede Schwelle der
Erkennung (`detector:`), alle Vorfilter (`filters:`), Universen,
Zyklus-Intervall, Batch-Größe, Cache-TTL und Benchmark sind ohne
Codeänderung konfigurierbar.
