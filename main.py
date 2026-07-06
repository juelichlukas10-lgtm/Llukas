"""Einstiegspunkt des Trading-Bots.

Beispiele:
    python main.py run
    python main.py list-strategies
    python main.py download --symbol BTC/USDT --timeframe 1h --start 2023-01-01
    python main.py backtest --strategy ema_crossover --symbol BTC/USDT \\
        --timeframe 1h --start 2023-01-01 --download
    python main.py dashboard
"""

from tradingbot.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
