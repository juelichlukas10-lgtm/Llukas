# Installation

## Voraussetzungen

- **Python ≥ 3.12** (getestet mit 3.12 und 3.14)
- pip
- Optional: Docker + Docker Compose
- Optional: PostgreSQL (SQLite ist Standard und benötigt keine Installation)

## Windows

```powershell
# Repository-Ordner öffnen
cd "D:\Trading bot"

# Virtuelle Umgebung (empfohlen)
python -m venv .venv
.venv\Scripts\activate

# Abhängigkeiten
pip install -r requirements.txt

# Umgebungsvariablen
copy .env.example .env
notepad .env    # API-Keys eintragen (nur für Live-Trading nötig)

# Test der Installation
python -m pytest tests/
python main.py list-strategies
```

## Linux / macOS

```bash
cd /pfad/zum/projekt

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt

cp .env.example .env
nano .env

python -m pytest tests/
python main.py list-strategies
```

## Installation als Paket (optional)

```bash
pip install -e .                 # Kernfunktionen
pip install -e ".[dashboard]"    # + Streamlit/Plotly
pip install -e ".[postgres]"     # + PostgreSQL-Treiber
pip install -e ".[dev]"          # + Test-/Lint-Tools
```

Danach steht der Befehl `tradingbot` direkt zur Verfügung
(äquivalent zu `python main.py`).

## Abhängigkeiten (Kern)

| Paket | Zweck |
| --- | --- |
| `ccxt` | Einheitliche Börsen-API (REST + WebSocket) |
| `pandas` / `numpy` | Datenverarbeitung und Indikatoren |
| `pydantic` | Konfigurations-Validierung |
| `SQLAlchemy` | Persistenz (SQLite/PostgreSQL) |
| `aiohttp` | Async-HTTP (Benachrichtigungen) |
| `PyYAML` / `python-dotenv` | Konfiguration / Secrets |
| `pyarrow` | Parquet-Speicherung historischer Daten |
| `streamlit` / `plotly` | Dashboard |
| `pytest` / `pytest-asyncio` | Tests |

## Erste Schritte nach der Installation

1. `config/config.yaml` prüfen – Standard ist Paper-Trading auf Binance
   mit BTC/USDT und ETH/USDT.
2. Bot starten: `python main.py run`
3. Dashboard starten: `python main.py dashboard`
   (Standard: <http://localhost:8501>)

Für Live-Trading siehe [configuration.md](configuration.md#live-trading-aktivieren).
