"""Gemeinsame Pytest-Fixtures für alle Tests."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# Projektwurzel in den Pfad aufnehmen, damit `tradingbot` importierbar ist,
# auch ohne vorherige Installation via pip.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture()
def ohlcv_df() -> pd.DataFrame:
    """Deterministischer OHLCV-DataFrame mit 300 5-Minuten-Kerzen.

    Der Kursverlauf ist ein Sinus mit Drift plus reproduzierbarem Rauschen,
    sodass Indikatoren sowohl Trend- als auch Seitwärtsphasen sehen.
    """
    rng = np.random.default_rng(42)
    n = 300
    t = np.arange(n)
    base = 100.0 + 0.05 * t + 5.0 * np.sin(t / 15.0)
    noise = rng.normal(0, 0.3, n)
    close = base + noise
    open_ = np.concatenate(([close[0]], close[:-1]))
    high = np.maximum(open_, close) + np.abs(rng.normal(0, 0.2, n))
    low = np.minimum(open_, close) - np.abs(rng.normal(0, 0.2, n))
    volume = rng.uniform(50, 150, n)

    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    index = pd.DatetimeIndex([start + timedelta(minutes=5 * i) for i in range(n)], name="timestamp")
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=index,
    )


@pytest.fixture()
def config_yaml(tmp_path: Path) -> Path:
    """Minimale, gültige Konfigurationsdatei in einem Temp-Verzeichnis."""
    content = """
app:
  name: "TestBot"
trading:
  mode: "paper"
  exchange: "binance"
  symbols: ["BTC/USDT"]
  timeframe: "5m"
paper:
  initial_balance: 10000.0
strategies:
  active:
    - name: "ema_crossover"
      symbols: ["BTC/USDT"]
      timeframe: "5m"
      params:
        fast_period: 12
        slow_period: 26
"""
    path = tmp_path / "config.yaml"
    path.write_text(content, encoding="utf-8")
    return path
