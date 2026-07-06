"""Integrationstests: TradingEngine mit Mock-Exchange über den vollen Stack."""

from __future__ import annotations

import asyncio
from pathlib import Path

import numpy as np
import pytest

from tests.mocks import MockExchangeAdapter
from tradingbot.core.config import Config, load_config
from tradingbot.core.engine import TradingEngine
from tradingbot.exchange.factory import register_exchange, _REGISTRY


@pytest.fixture()
def integration_config(tmp_path: Path) -> Config:
    """Konfiguration mit Mock-Exchange und temporärer SQLite-Datenbank."""
    db_path = tmp_path / "test.db"
    content = f"""
app:
  name: "IntegrationTestBot"
trading:
  mode: "paper"
  exchange: "mockexchange"
  symbols: ["BTC/USDT"]
  timeframe: "5m"
  candle_history: 200
  loop_interval_seconds: 0.1
paper:
  initial_balance: 10000.0
strategies:
  active:
    - name: "ema_crossover"
      symbols: ["BTC/USDT"]
      timeframe: "5m"
      params:
        fast_period: 5
        slow_period: 15
risk:
  stop_loss: 0.05
  take_profit: 0.10
  max_daily_trades: 100
database:
  url: "sqlite:///{db_path.as_posix()}"
logging:
  dir: "{(tmp_path / 'logs').as_posix()}"
  console: false
"""
    path = tmp_path / "config.yaml"
    path.write_text(content, encoding="utf-8")
    return load_config(path, env_file=None)


@pytest.fixture()
def mock_exchange_registered():
    """Registriert einen Mock-Exchange mit Trendwechsel-Daten in der Factory."""
    # Kursverlauf mit klaren Trendwechseln, damit EMA-Signale entstehen.
    t = np.arange(400)
    prices = list(100.0 + 15.0 * np.sin(t / 25.0))
    adapter = MockExchangeAdapter(prices=prices)

    @register_exchange("mockexchange")
    def _build(config: Config) -> MockExchangeAdapter:  # noqa: ANN001
        return adapter

    yield adapter
    _REGISTRY.pop("mockexchange", None)


class TestEngineIntegration:
    async def test_full_lifecycle(self, integration_config: Config, mock_exchange_registered) -> None:
        """Engine startet, verarbeitet Kerzen/Signale und stoppt sauber."""
        engine = TradingEngine(integration_config)
        await engine.start()
        try:
            # Streams des Mocks laufen sofort durch alle Kerzen.
            await asyncio.sleep(0.5)
            status = engine.status
            assert status["running"] is True
            assert status["strategies"] == ["ema_crossover"]
            assert status["equity"] > 0
        finally:
            await engine.stop()
        assert engine.status["running"] is False

    async def test_signals_produce_trades(
        self, integration_config: Config, mock_exchange_registered
    ) -> None:
        """Auf den Sinus-Daten müssen EMA-Signale zu Positionen/Trades führen."""
        engine = TradingEngine(integration_config)
        await engine.start()
        try:
            await asyncio.sleep(1.0)
            status = engine.status
            total_activity = int(status["open_positions"]) + int(status["closed_trades"])
            assert total_activity > 0, "Es sollten Positionen oder Trades entstanden sein"
        finally:
            await engine.stop()

    async def test_persistence(self, integration_config: Config, mock_exchange_registered) -> None:
        """Strategien und Performance-Snapshots landen in der Datenbank."""
        from tradingbot.database.repository import Database

        engine = TradingEngine(integration_config)
        await engine.start()
        try:
            await asyncio.sleep(0.5)
        finally:
            await engine.stop()

        db = Database(url=integration_config.database.url)
        try:
            strategies = db.get_strategies()
            assert any(s.name == "ema_crossover" for s in strategies)
        finally:
            db.close()
