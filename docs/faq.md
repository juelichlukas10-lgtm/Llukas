# FAQ – Häufige Fragen

### Der Bot startet nicht im Live-Modus – warum?

Live-Trading erfordert **beide** Schalter in `config/config.yaml`:

```yaml
trading:
  mode: "live"
  live_trading_confirmed: true
```

Zusätzlich müssen gültige API-Keys der Börse in der `.env` stehen
(z. B. `BINANCE_API_KEY`/`BINANCE_API_SECRET`). Ohne Bestätigung bricht
die Konfigurations-Validierung mit einer klaren Fehlermeldung ab – das
ist Absicht (Sicherheitsnetz).

### Brauche ich API-Keys für den Paper-Modus?

Nein. Der Paper-Modus nutzt ausschließlich öffentliche Marktdaten-
Endpunkte. Keys werden nur für Live-Trading benötigt.

### Woher kommen die Marktdaten im Paper-Modus?

Live von der konfigurierten Börse (WebSocket + REST). Nur die
Orderausführung und Kontoführung werden lokal simuliert – inklusive
Kommission, Slippage und Spread.

### Wie füge ich eine neue Strategie hinzu?

Eine einzelne Python-Datei in `tradingbot/strategies/` mit einer
`@register_strategy("name")`-dekorierten Klasse ablegen. Kein weiterer
Code nötig. Details: [strategies.md](strategies.md).

### Wie füge ich eine neue Börse hinzu?

1. Jede von CCXT Pro unterstützte Börse funktioniert oft direkt:
   `trading.exchange` auf die CCXT-ID setzen (z. B. `"kucoin"`).
2. Für Spezialfälle eine Factory registrieren:

   ```python
   from tradingbot.exchange import register_exchange

   @register_exchange("myexchange")
   def build(config):
       return MyExchangeAdapter(...)
   ```

### Welche Datenbank soll ich verwenden?

SQLite (Standard) reicht für Einzelbetrieb völlig aus. PostgreSQL
empfiehlt sich bei mehreren Bot-Instanzen oder externem Dashboard:
`TRADINGBOT_DB_URL=postgresql+psycopg2://user:pass@host:5432/tradingbot`.

### Der WebSocket bricht ab – gehen Daten verloren?

Nein. Alle Streams verbinden sich automatisch neu (mit Backoff).
Nach dem Reconnect werden die Kerzen-Puffer über REST wieder
aufgefüllt. Abgestürzte Stream-Tasks startet der Stream-Manager neu.

### Warum handelt der Bot nicht?

Häufigste Ursachen (im Log bzw. Dashboard → Logs sichtbar):

1. **Warmup:** Strategien brauchen `required_history` Kerzen Kontext.
2. **Risiko-Limits:** Max. Positionen, Tages-Trade-Limit, Cooldown nach
   Verlustserie oder ein Drawdown-/Tagesverlust-Stopp (`halted`).
3. **Kein Signal:** Die Marktlage erfüllt die Einstiegsbedingungen nicht.
4. **Positionsgröße 0:** z. B. Kelly ohne ausreichende Trade-Historie.

### Was bedeutet „Handelsstopp" und wie hebe ich ihn auf?

Bei Erreichen von `max_drawdown` oder `max_daily_loss` stoppt der Bot
neue Einstiege (offene Positionen werden weiter überwacht). Der
Tagesverlust-Stopp löst sich am nächsten UTC-Tag automatisch; der
Drawdown-Stopp erfordert Neustart oder bewusstes Eingreifen – prüfe
zuerst, warum er ausgelöst wurde.

### Wie aussagekräftig sind die Backtests?

Die Engine vermeidet Look-Ahead, rechnet Kosten intra-bar und nutzt
dieselben Strategie-Klassen wie der Live-Betrieb. Dennoch gilt:
Slippage in illiquiden Märkten, Latenz und Teil-Fills lassen sich nie
perfekt simulieren. Nutze die **Walk-Forward-Analyse** und validiere
anschließend im Paper-Modus.

### Wie sichere ich meine Daten?

`storage/` (historische Daten, SQLite-DB, Backtest-Exporte) und `logs/`
sichern. Bei PostgreSQL zusätzlich reguläre DB-Dumps.

### Unter Windows erscheinen Umlaute im Terminal falsch.

Das ist eine Konsolen-Codepage-Frage, kein Bot-Fehler:
`chcp 65001` ausführen oder `PYTHONIOENCODING=utf-8` setzen.
Logdateien sind immer UTF-8.

### Wie führe ich die Tests aus?

```bash
python -m pytest tests/            # alle 210 Tests
python -m pytest tests/unit -q     # nur Unit-Tests
```
