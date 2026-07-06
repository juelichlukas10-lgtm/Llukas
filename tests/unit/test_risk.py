"""Unit-Tests für Risiko-Management und Positionsgrößen."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tradingbot.core.config import LossStreakCooldownConfig, RiskConfig, SizingConfig
from tradingbot.core.enums import PositionSide, SignalAction, SizingMethod
from tradingbot.core.exceptions import RiskError
from tradingbot.core.models import Position, Signal, Trade
from tradingbot.risk.manager import RiskManager
from tradingbot.risk.sizing import PositionSizer


def _signal(action: SignalAction = SignalAction.BUY, price: float = 100.0, **kwargs) -> Signal:
    return Signal(
        action=action,
        symbol="BTC/USDT",
        strategy="test",
        timestamp=datetime.now(timezone.utc),
        price=price,
        **kwargs,
    )


def _trade(pnl: float) -> Trade:
    now = datetime.now(timezone.utc)
    return Trade(
        symbol="BTC/USDT",
        side=PositionSide.LONG,
        amount=1.0,
        entry_price=100.0,
        exit_price=100.0 + pnl,
        pnl=pnl,
        fees=0.0,
        strategy="test",
        opened_at=now,
        closed_at=now,
    )


class TestPositionSizer:
    def _sizer(self, method: SizingMethod, **overrides) -> PositionSizer:
        sizing = SizingConfig(method=method, **overrides)
        risk = RiskConfig(risk_per_trade=0.01, stop_loss=0.02, max_leverage=10.0)
        return PositionSizer(sizing, risk)

    def test_fixed(self) -> None:
        sizer = self._sizer(SizingMethod.FIXED, fixed_amount=500.0)
        assert sizer.compute(equity=10_000.0, price=100.0) == pytest.approx(5.0)

    def test_percent_risk_with_stop(self) -> None:
        sizer = self._sizer(SizingMethod.PERCENT_RISK)
        # Risiko 1% von 10000 = 100; Stop-Distanz 5 -> 20 Einheiten.
        amount = sizer.compute(equity=10_000.0, price=100.0, stop_loss=95.0)
        assert amount == pytest.approx(20.0)

    def test_percent_risk_fallback_stop(self) -> None:
        sizer = self._sizer(SizingMethod.PERCENT_RISK)
        # Fallback-Stop 2% -> Distanz 2 -> 100/2 = 50 Einheiten.
        amount = sizer.compute(equity=10_000.0, price=100.0)
        assert amount == pytest.approx(50.0)

    def test_atr_sizing(self) -> None:
        sizer = self._sizer(SizingMethod.ATR, atr_risk_multiple=2.0)
        # Risiko 100; Stop-Distanz 2*2.5=5 -> 20 Einheiten.
        amount = sizer.compute(equity=10_000.0, price=100.0, atr_value=2.5)
        assert amount == pytest.approx(20.0)

    def test_kelly_with_history(self) -> None:
        sizer = self._sizer(SizingMethod.KELLY, kelly_fraction=0.5, kelly_lookback=30)
        # 60% Winrate, avg win 10, avg loss 5 -> Kelly = 0.6 - 0.4/2 = 0.4; * 0.5 = 0.2.
        history = [_trade(10.0)] * 6 + [_trade(-5.0)] * 4
        amount = sizer.compute(equity=10_000.0, price=100.0, trade_history=history)
        assert amount == pytest.approx(10_000.0 * 0.2 / 100.0)

    def test_kelly_all_losses_is_zero(self) -> None:
        sizer = self._sizer(SizingMethod.KELLY)
        history = [_trade(-5.0)] * 10
        assert sizer.compute(10_000.0, 100.0, trade_history=history) == 0.0

    def test_kelly_insufficient_history_falls_back(self) -> None:
        sizer = self._sizer(SizingMethod.KELLY)
        amount = sizer.compute(10_000.0, 100.0, trade_history=[_trade(5.0)])
        assert amount == pytest.approx(50.0)  # percent_risk-Fallback

    def test_leverage_cap(self) -> None:
        sizer = self._sizer(SizingMethod.PERCENT_RISK)
        # Sehr enger Stop würde riesige Position ergeben -> Hebel-Limit greift.
        amount = sizer.compute(equity=10_000.0, price=100.0, stop_loss=99.99, max_leverage=2.0)
        assert amount == pytest.approx(10_000.0 * 2.0 / 100.0)

    def test_invalid_inputs(self) -> None:
        sizer = self._sizer(SizingMethod.FIXED)
        with pytest.raises(RiskError):
            sizer.compute(equity=0.0, price=100.0)
        with pytest.raises(RiskError):
            sizer.compute(equity=1000.0, price=-1.0)


class TestRiskManagerEntry:
    def _manager(self, **overrides) -> RiskManager:
        config = RiskConfig(**overrides) if overrides else RiskConfig()
        return RiskManager(config, initial_equity=10_000.0)

    def test_approves_valid_entry(self) -> None:
        decision = self._manager().evaluate_entry(_signal(), open_positions=0)
        assert decision.approved
        assert decision.stop_loss == pytest.approx(98.0)   # 2% Default
        assert decision.take_profit == pytest.approx(104.0)  # 4% Default

    def test_strategy_stops_take_precedence(self) -> None:
        decision = self._manager().evaluate_entry(
            _signal(stop_loss=97.0, take_profit=110.0), open_positions=0
        )
        assert decision.stop_loss == pytest.approx(97.0)
        assert decision.take_profit == pytest.approx(110.0)

    def test_short_default_stops(self) -> None:
        decision = self._manager().evaluate_entry(
            _signal(action=SignalAction.SELL), open_positions=0
        )
        assert decision.stop_loss == pytest.approx(102.0)
        assert decision.take_profit == pytest.approx(96.0)

    def test_rejects_max_positions(self) -> None:
        manager = self._manager(max_open_positions=2)
        decision = manager.evaluate_entry(_signal(), open_positions=2)
        assert not decision.approved
        assert "offene Positionen" in decision.reason

    def test_rejects_daily_trade_limit(self) -> None:
        manager = self._manager(max_daily_trades=2)
        manager.record_trade(_trade(1.0))
        manager.record_trade(_trade(1.0))
        decision = manager.evaluate_entry(_signal(), open_positions=0)
        assert not decision.approved
        assert "Trade-Limit" in decision.reason

    def test_rejects_non_entry_signal(self) -> None:
        decision = self._manager().evaluate_entry(
            _signal(action=SignalAction.CLOSE_LONG), open_positions=0
        )
        assert not decision.approved


class TestRiskManagerHalts:
    def test_max_drawdown_halts(self) -> None:
        manager = RiskManager(RiskConfig(max_drawdown=0.10), initial_equity=10_000.0)
        assert manager.update_equity(9_500.0) is None
        reason = manager.update_equity(8_900.0)  # 11% Drawdown
        assert reason is not None and "Drawdown" in reason
        halted, _ = manager.is_halted
        assert halted
        assert not manager.evaluate_entry(_signal(), open_positions=0).approved

    def test_daily_loss_halts(self) -> None:
        manager = RiskManager(RiskConfig(max_daily_loss=0.03), initial_equity=10_000.0)
        manager.record_trade(_trade(-350.0))  # 3.5% Tagesverlust
        reason = manager.update_equity(9_650.0)
        assert reason is not None and "Tagesverlust" in reason

    def test_loss_streak_cooldown(self) -> None:
        config = RiskConfig(
            loss_streak_cooldown=LossStreakCooldownConfig(
                max_consecutive_losses=3, cooldown_minutes=60
            )
        )
        manager = RiskManager(config, initial_equity=10_000.0)
        for _ in range(3):
            manager.record_trade(_trade(-10.0))
        halted, reason = manager.is_halted
        assert halted and "Cooldown" in reason

    def test_win_resets_streak(self) -> None:
        config = RiskConfig(
            loss_streak_cooldown=LossStreakCooldownConfig(
                max_consecutive_losses=3, cooldown_minutes=60
            )
        )
        manager = RiskManager(config, initial_equity=10_000.0)
        manager.record_trade(_trade(-10.0))
        manager.record_trade(_trade(-10.0))
        manager.record_trade(_trade(5.0))
        manager.record_trade(_trade(-10.0))
        halted, _ = manager.is_halted
        assert not halted

    def test_reset_halt(self) -> None:
        manager = RiskManager(RiskConfig(max_drawdown=0.05), initial_equity=10_000.0)
        manager.update_equity(9_000.0)
        assert manager.is_halted[0]
        manager.reset_halt()
        assert not manager.is_halted[0]


class TestExitRules:
    def _position(self, side: PositionSide = PositionSide.LONG, **kwargs) -> Position:
        defaults = dict(symbol="BTC/USDT", amount=1.0, entry_price=100.0)
        defaults.update(kwargs)
        return Position(side=side, **defaults)  # type: ignore[arg-type]

    def test_stop_loss_hit_long(self) -> None:
        manager = RiskManager(RiskConfig(), initial_equity=10_000.0)
        position = self._position(stop_loss=95.0)
        assert manager.check_exit(position, 96.0) is None
        assert manager.check_exit(position, 94.9) == "stop_loss"

    def test_take_profit_hit_short(self) -> None:
        manager = RiskManager(RiskConfig(), initial_equity=10_000.0)
        position = self._position(side=PositionSide.SHORT, take_profit=90.0)
        assert manager.check_exit(position, 89.5) == "take_profit"

    def test_trailing_stop_moves_up_only(self) -> None:
        manager = RiskManager(RiskConfig(), initial_equity=10_000.0)
        position = self._position(trailing_stop=0.05, stop_loss=90.0)
        manager.check_exit(position, 120.0)
        assert position.stop_loss == pytest.approx(114.0)
        # Kursrückgang zieht den Stop NICHT nach unten.
        result = manager.check_exit(position, 115.0)
        assert result is None
        assert position.stop_loss == pytest.approx(114.0)
        assert manager.check_exit(position, 113.0) == "stop_loss"

    def test_break_even(self) -> None:
        manager = RiskManager(
            RiskConfig(break_even_trigger=0.02), initial_equity=10_000.0
        )
        position = self._position(stop_loss=95.0)
        manager.check_exit(position, 101.0)
        assert position.stop_loss == pytest.approx(95.0)  # Trigger noch nicht erreicht
        manager.check_exit(position, 102.5)
        assert position.stop_loss == pytest.approx(100.0)  # auf Einstand gezogen
        assert position.break_even_done

    def test_break_even_short(self) -> None:
        manager = RiskManager(
            RiskConfig(break_even_trigger=0.02), initial_equity=10_000.0
        )
        position = self._position(side=PositionSide.SHORT, stop_loss=105.0)
        manager.check_exit(position, 97.5)
        assert position.stop_loss == pytest.approx(100.0)
