# Deployment

## Streamlit Community Cloud (Dashboard)

Das Dashboard lässt sich kostenlos auf [share.streamlit.io](https://share.streamlit.io)
hosten. Da Streamlit Cloud ein **flüchtiges Dateisystem** hat (SQLite und
lokale Kerzendaten überleben keinen Neustart), betreibt man den Bot lokal/
auf einem eigenen Server gegen eine **externe PostgreSQL-Datenbank** – das
Cloud-Dashboard liest dieselbe Datenbank nur lesend aus.

### 1. Kostenlose Postgres-Datenbank anlegen (Neon)

1. Auf [neon.tech](https://neon.tech) registrieren (kostenloses Kontingent).
2. „New Project" erstellen, Datenbankname z. B. `tradingbot`.
3. Den angezeigten Connection-String kopieren, z. B.:
   ```
   postgresql://user:password@ep-xyz-123.eu-central-1.aws.neon.tech/tradingbot?sslmode=require
   ```
4. In eine SQLAlchemy-URL umwandeln (`postgresql` → `postgresql+psycopg2`):
   ```
   postgresql+psycopg2://user:password@ep-xyz-123.eu-central-1.aws.neon.tech/tradingbot?sslmode=require
   ```

### 2. Bot lokal auf Postgres umstellen

In der lokalen `.env` ergänzen:

```
TRADINGBOT_DB_URL=postgresql+psycopg2://user:password@ep-xyz-123.eu-central-1.aws.neon.tech/tradingbot?sslmode=require
```

`TRADINGBOT_DB_URL` überschreibt `database.url` aus der `config.yaml`
automatisch (siehe [configuration.md](configuration.md#database--persistenz)).
Bot neu starten – Trades, Orders und Performance landen jetzt in Neon
statt in der lokalen SQLite-Datei.

### 3. Repo auf GitHub pushen (falls noch nicht geschehen)

Streamlit Cloud deployt direkt aus einem GitHub-Repository. `.env` wird
**nicht** mitcommitted (steht in `.gitignore`) – Secrets kommen auf der
Cloud über einen separaten Mechanismus (Schritt 5).

### 4. App auf Streamlit Cloud anlegen

1. Auf [share.streamlit.io](https://share.streamlit.io) einloggen (GitHub-Login).
2. „New app" → Repository und Branch (`main`) auswählen.
3. **Main file path:** `tradingbot/dashboard/app.py`
4. „Deploy!" klicken.

### 5. Datenbank-Secret in Streamlit Cloud hinterlegen

In der App-Verwaltung auf Streamlit Cloud: **Settings → Secrets** und
folgendes eintragen (TOML-Format):

```toml
TRADINGBOT_DB_URL = "postgresql+psycopg2://user:password@ep-xyz-123.eu-central-1.aws.neon.tech/tradingbot?sslmode=require"
```

Das Dashboard liest Streamlit-Secrets automatisch als Umgebungsvariable
ein (Bridge in `tradingbot/dashboard/app.py`) und verbindet sich damit
mit derselben Datenbank wie der lokal laufende Bot. Nach dem Speichern
startet die App automatisch neu.

> Lokal für Tests kann dieselbe Secret-Struktur auch in
> `.streamlit/secrets.toml` abgelegt werden – diese Datei gehört **nicht**
> ins Git-Repo (analog zu `.env`).

## Docker (empfohlen)

```bash
# Image bauen und Bot starten (Paper-Modus gemäß config/config.yaml)
docker compose up -d bot

# Dashboard auf http://localhost:8501
docker compose up -d dashboard

# Optional: PostgreSQL statt SQLite
docker compose --profile postgres up -d
# und in .env:
# TRADINGBOT_DB_URL=postgresql+psycopg2://tradingbot:tradingbot@postgres:5432/tradingbot

# Logs verfolgen / Status
docker compose logs -f bot
docker compose ps

# Update einspielen
docker compose build && docker compose up -d
```

Persistente Daten (`storage/`, `logs/`) und die Konfiguration werden als
Volumes gemountet – Container können jederzeit neu erstellt werden.

## Linux-Server (systemd)

```ini
# /etc/systemd/system/tradingbot.service
[Unit]
Description=TradingBot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=tradingbot
WorkingDirectory=/opt/tradingbot
ExecStart=/opt/tradingbot/.venv/bin/python main.py run
Restart=on-failure
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now tradingbot
journalctl -u tradingbot -f
```

## Windows

Dauerbetrieb z. B. über die Aufgabenplanung („Bei Systemstart",
Programm: `python`, Argumente: `main.py run`, Startordner: Projektpfad)
oder in einer Terminal-Session:

```powershell
cd "D:\Trading bot"
.venv\Scripts\activate
python main.py run
```

## macOS (launchd)

```xml
<!-- ~/Library/LaunchAgents/com.tradingbot.plist -->
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.tradingbot</string>
    <key>WorkingDirectory</key><string>/Users/ich/tradingbot</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/ich/tradingbot/.venv/bin/python</string>
        <string>main.py</string>
        <string>run</string>
    </array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/com.tradingbot.plist
```

## Betriebs-Checkliste

1. **Immer zuerst im Paper-Modus** laufen lassen und Verhalten im
   Dashboard beobachten.
2. Backtests + Walk-Forward für jede Strategie/Parameter-Kombination.
3. Benachrichtigungen aktivieren (`notifications`), damit Fehler und
   Risiko-Stopps sofort auffallen.
4. `.env` restriktiv berechtigen (`chmod 600 .env`); API-Keys der Börse
   ohne Auszahlungsrechte anlegen und optional per IP-Whitelist sichern.
5. Live-Start mit kleinem Kapital und konservativen Limits
   (`max_daily_loss`, `max_drawdown`).
6. Log-Rotation ist eingebaut; `logs/` und `storage/` regelmäßig sichern.
