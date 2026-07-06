"""Strukturiertes Logging mit rotierenden Dateien.

Bietet eine zentrale :func:`setup_logging`-Funktion sowie
:func:`get_logger` für modulweite Logger. Unterstützt Konsolen- und
Dateiausgabe, Log-Rotation und optionales JSON-Format.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import sys
from datetime import datetime, timezone
from pathlib import Path

from tradingbot.core.config import LoggingConfig

_ROOT_LOGGER_NAME = "tradingbot"
_configured = False


class JsonFormatter(logging.Formatter):
    """Formatter, der Log-Records als JSON-Zeilen ausgibt."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            payload["exception"] = self.formatException(record.exc_info)
        extra = getattr(record, "extra_data", None)
        if isinstance(extra, dict):
            payload.update(extra)
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(config: LoggingConfig | None = None) -> logging.Logger:
    """Initialisiert das Logging-System (idempotent).

    Args:
        config: Logging-Konfiguration; None verwendet Defaults.

    Returns:
        Der konfigurierte Root-Logger des Bots (``tradingbot``).
    """
    global _configured
    cfg = config or LoggingConfig()
    logger = logging.getLogger(_ROOT_LOGGER_NAME)

    if _configured:
        logger.setLevel(cfg.level)
        return logger

    logger.setLevel(cfg.level)
    logger.propagate = False

    if cfg.json_format:
        formatter: logging.Formatter = JsonFormatter()
    else:
        formatter = logging.Formatter(
            fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    log_dir = Path(cfg.dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / cfg.file_name,
        maxBytes=cfg.max_bytes,
        backupCount=cfg.backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    if cfg.console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    _configured = True
    return logger


def get_logger(name: str) -> logging.Logger:
    """Liefert einen Kind-Logger unterhalb des Bot-Root-Loggers.

    Args:
        name: Modulname, üblicherweise ``__name__``.

    Returns:
        Logger mit Namen ``tradingbot.<name>`` (Doppel-Präfix wird vermieden).
    """
    if name.startswith(_ROOT_LOGGER_NAME):
        return logging.getLogger(name)
    return logging.getLogger(f"{_ROOT_LOGGER_NAME}.{name}")
