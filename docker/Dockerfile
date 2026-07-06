# TradingBot – Container-Image
#
# Build (aus dem Projektwurzelverzeichnis):
#   docker build -f docker/Dockerfile -t tradingbot .
#
# Start (Paper-Modus):
#   docker run --env-file .env -v ./storage:/app/storage -v ./logs:/app/logs tradingbot

FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Abhängigkeiten zuerst (Docker-Layer-Cache).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Anwendung.
COPY tradingbot/ tradingbot/
COPY config/ config/
COPY main.py pyproject.toml ./

# Laufzeitverzeichnisse.
RUN mkdir -p storage/historical storage/backtests logs

# Nicht als root laufen.
RUN useradd --create-home botuser && chown -R botuser:botuser /app
USER botuser

# Standard: Bot im (Paper-)Modus gemäß config/config.yaml.
ENTRYPOINT ["python", "main.py"]
CMD ["run"]
