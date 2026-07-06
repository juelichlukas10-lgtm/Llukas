"""Kommandozeilen-Interface des Trading-Bots.

Befehle:
    run              Bot starten (Paper-Modus als Standard).
    download         Historische Daten herunterladen.
    backtest         Backtest einer Strategie ausführen.
    optimize         Parameter-Optimierung (Grid/Random Search).
    walkforward      Walk-Forward-Analyse.
    list-strategies  Verfügbare Strategien anzeigen.
    dashboard        Streamlit-Dashboard starten.

Beispiele:
    python main.py run
    python main.py download --symbol BTC/USDT --timeframe 1h --start 2023-01-01
    python main.py backtest --strategy ema_crossover --symbol BTC/USDT \\
        --timeframe 1h --start 2023-01-01 --end 2024-01-01
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from tradingbot.core.config import Config, load_config
from tradingbot.core.enums import Timeframe
from tradingbot.core.logging import get_logger, setup_logging

logger = get_logger(__name__)


def _parse_date(value: str) -> datetime:
    """Parst ``YYYY-MM-DD`` (oder ISO-Format) als UTC-Datetime."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _parse_params(value: str | None) -> dict:
    """Parst einen JSON-Parameterstring (None -> leeres Dict)."""
    if not value:
        return {}
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise argparse.ArgumentTypeError("Parameter müssen ein JSON-Objekt sein")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    """Erstellt den Argument-Parser mit allen Sub-Kommandos."""
    parser = argparse.ArgumentParser(
        prog="tradingbot",
        description="Professioneller algorithmischer Trading-Bot",
    )
    parser.add_argument(
        "--config",
        default="config/config.yaml",
        help="Pfad zur YAML-Konfiguration (Standard: config/config.yaml)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("run", help="Bot starten (Paper-Modus als Standard)")

    p_download = sub.add_parser("download", help="Historische Daten herunterladen")
    p_download.add_argument("--symbol", required=True, action="append", dest="symbols")
    p_download.add_argument("--timeframe", required=True, action="append", dest="timeframes")
    p_download.add_argument("--start", required=True, type=_parse_date)
    p_download.add_argument("--end", type=_parse_date, default=None)

    p_backtest = sub.add_parser("backtest", help="Backtest ausführen")
    p_backtest.add_argument("--strategy", required=True)
    p_backtest.add_argument("--symbol", required=True, action="append", dest="symbols")
    p_backtest.add_argument("--timeframe", required=True)
    p_backtest.add_argument("--start", required=True, type=_parse_date)
    p_backtest.add_argument("--end", type=_parse_date, default=None)
    p_backtest.add_argument("--params", type=str, default=None, help="Strategie-Parameter als JSON")
    p_backtest.add_argument("--download", action="store_true", help="Fehlende Daten herunterladen")

    p_optimize = sub.add_parser("optimize", help="Parameter-Optimierung")
    p_optimize.add_argument("--strategy", required=True)
    p_optimize.add_argument("--symbol", required=True, action="append", dest="symbols")
    p_optimize.add_argument("--timeframe", required=True)
    p_optimize.add_argument("--start", required=True, type=_parse_date)
    p_optimize.add_argument("--end", type=_parse_date, default=None)
    p_optimize.add_argument(
        "--grid", required=True, type=str,
        help='Parameterraum als JSON, z. B. {"fast_period": [8, 12], "slow_period": [21, 26]}',
    )
    p_optimize.add_argument("--method", choices=["grid", "random"], default="grid")
    p_optimize.add_argument("--iterations", type=int, default=50, help="Nur für random")
    p_optimize.add_argument("--metric", default="sharpe_ratio")
    p_optimize.add_argument("--download", action="store_true")

    p_wf = sub.add_parser("walkforward", help="Walk-Forward-Analyse")
    p_wf.add_argument("--strategy", required=True)
    p_wf.add_argument("--symbol", required=True, action="append", dest="symbols")
    p_wf.add_argument("--timeframe", required=True)
    p_wf.add_argument("--start", required=True, type=_parse_date)
    p_wf.add_argument("--end", type=_parse_date, default=None)
    p_wf.add_argument("--grid", required=True, type=str)
    p_wf.add_argument("--windows", type=int, default=4)
    p_wf.add_argument("--train-ratio", type=float, default=0.75)
    p_wf.add_argument("--metric", default="sharpe_ratio")
    p_wf.add_argument("--download", action="store_true")

    sub.add_parser("list-strategies", help="Verfügbare Strategien anzeigen")
    sub.add_parser("dashboard", help="Streamlit-Dashboard starten")
    return parser


# ----------------------------------------------------------------------
# Befehls-Implementierungen
# ----------------------------------------------------------------------


def _cmd_run(config: Config) -> int:
    from tradingbot.core.engine import TradingEngine

    engine = TradingEngine(config)
    try:
        asyncio.run(engine.run_forever())
    except KeyboardInterrupt:
        logger.info("Abbruch durch Benutzer (Strg+C)")
    return 0


async def _load_data(
    config: Config,
    symbols: list[str],
    timeframe: Timeframe,
    start: datetime,
    end: datetime | None,
    download: bool,
) -> dict:
    """Lädt Backtest-Daten aus dem lokalen Speicher (optional mit Download)."""
    import pandas as pd

    from tradingbot.core.config import load_credentials
    from tradingbot.data.downloader import HistoricalDownloader
    from tradingbot.data.storage import CandleStorage
    from tradingbot.exchange.ccxt_adapter import CcxtExchangeAdapter

    storage = CandleStorage(config.data.storage_dir)
    exchange_name = config.trading.exchange
    data: dict[str, pd.DataFrame] = {}

    if download:
        adapter = CcxtExchangeAdapter(
            exchange_id=exchange_name,
            credentials=load_credentials(exchange_name),
            settings=config.exchange_settings(exchange_name),
            market_type=config.trading.market_type,
        )
        await adapter.connect()
        try:
            downloader = HistoricalDownloader(
                adapter, storage, batch_size=config.data.download_batch_size
            )
            for symbol in symbols:
                await downloader.download(symbol, timeframe, start, end)
        finally:
            await adapter.close()

    for symbol in symbols:
        df = storage.load(
            exchange_name, symbol, timeframe,
            start=pd.Timestamp(start),
            end=pd.Timestamp(end) if end else None,
        )
        if df.empty:
            raise SystemExit(
                f"Keine lokalen Daten für {symbol} {timeframe.value} auf {exchange_name}. "
                f"Mit --download herunterladen oder zuerst 'download' ausführen."
            )
        data[symbol] = df
    return data


def _cmd_download(config: Config, args: argparse.Namespace) -> int:
    async def _run() -> None:
        from tradingbot.core.config import load_credentials
        from tradingbot.data.downloader import HistoricalDownloader
        from tradingbot.data.storage import CandleStorage
        from tradingbot.exchange.ccxt_adapter import CcxtExchangeAdapter

        exchange_name = config.trading.exchange
        adapter = CcxtExchangeAdapter(
            exchange_id=exchange_name,
            credentials=load_credentials(exchange_name),
            settings=config.exchange_settings(exchange_name),
            market_type=config.trading.market_type,
        )
        await adapter.connect()
        try:
            storage = CandleStorage(config.data.storage_dir)
            downloader = HistoricalDownloader(
                adapter, storage, batch_size=config.data.download_batch_size
            )
            timeframes = [Timeframe.from_string(tf) for tf in args.timeframes]
            results = await downloader.download_many(args.symbols, timeframes, args.start, args.end)
            for (symbol, timeframe), df in results.items():
                print(f"{symbol} {timeframe.value}: {len(df)} Kerzen lokal verfügbar")
        finally:
            await adapter.close()

    asyncio.run(_run())
    return 0


def _print_report(report_dict: dict) -> None:
    """Gibt einen Kennzahlen-Bericht tabellarisch aus."""
    print("\n=== Performance-Bericht ===")
    for key, value in report_dict.items():
        print(f"  {key:<20} {value}")


def _cmd_backtest(config: Config, args: argparse.Namespace) -> int:
    from tradingbot.backtesting.engine import BacktestEngine, BacktestSettings
    from tradingbot.database.repository import Database
    from tradingbot.strategies.registry import create_strategy

    timeframe = Timeframe.from_string(args.timeframe)
    data = asyncio.run(
        _load_data(config, args.symbols, timeframe, args.start, args.end, args.download)
    )
    strategy = create_strategy(
        args.strategy, symbols=args.symbols, timeframe=timeframe, params=_parse_params(args.params)
    )
    settings = BacktestSettings(
        initial_balance=config.backtest.initial_balance,
        commission_rate=config.backtest.commission_rate,
        slippage_rate=config.backtest.slippage_rate,
        spread_rate=config.backtest.spread_rate,
        leverage=config.backtest.leverage,
    )
    engine = BacktestEngine(settings, config.risk, config.sizing)
    result = engine.run(strategy, data)
    _print_report(result.report.to_dict())

    # Ergebnis persistieren.
    db = Database(url=config.database.url)
    try:
        backtest_id = db.save_backtest(
            strategy=args.strategy,
            symbols=args.symbols,
            timeframe=timeframe,
            params=result.params,
            start=args.start,
            end=args.end or datetime.now(timezone.utc),
            initial_balance=settings.initial_balance,
            final_equity=(
                float(result.equity_curve.iloc[-1])
                if not result.equity_curve.empty
                else settings.initial_balance
            ),
            metrics=result.report.to_dict(),
        )
        print(f"\nBacktest gespeichert (ID: {backtest_id})")
    finally:
        db.close()

    # Equity-Kurve als CSV ablegen.
    out_dir = Path(config.data.backtest_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"equity_{args.strategy}_{timeframe.value}.csv"
    result.equity_curve.to_csv(csv_path, header=True)
    print(f"Equity-Kurve: {csv_path}")
    return 0


def _cmd_optimize(config: Config, args: argparse.Namespace) -> int:
    from tradingbot.backtesting.engine import BacktestEngine, BacktestSettings
    from tradingbot.backtesting.optimizer import GridSearchOptimizer, RandomSearchOptimizer
    from tradingbot.strategies.registry import get_strategy_class

    timeframe = Timeframe.from_string(args.timeframe)
    data = asyncio.run(
        _load_data(config, args.symbols, timeframe, args.start, args.end, args.download)
    )
    space = json.loads(args.grid)
    # JSON kennt keine Tupel: 2er-Listen bei random als (min, max) interpretieren.
    if args.method == "random":
        space = {
            k: tuple(v) if isinstance(v, list) and len(v) == 2 and all(
                isinstance(x, (int, float)) for x in v
            ) else v
            for k, v in space.items()
        }

    engine = BacktestEngine(
        BacktestSettings(
            initial_balance=config.backtest.initial_balance,
            commission_rate=config.backtest.commission_rate,
            slippage_rate=config.backtest.slippage_rate,
            spread_rate=config.backtest.spread_rate,
            leverage=config.backtest.leverage,
        ),
        config.risk,
        config.sizing,
    )
    strategy_class = get_strategy_class(args.strategy)

    if args.method == "grid":
        optimizer = GridSearchOptimizer(
            engine, strategy_class, args.symbols, timeframe, metric=args.metric
        )
        result = optimizer.optimize(space, data)
    else:
        optimizer = RandomSearchOptimizer(
            engine, strategy_class, args.symbols, timeframe, metric=args.metric
        )
        result = optimizer.optimize(space, data, n_iterations=args.iterations)

    print(f"\nBeste Parameter ({args.metric} = {result.best_score:.4f}):")
    print(json.dumps(result.best_params, indent=2))
    print("\nTop 10 Kombinationen:")
    df = result.to_dataframe().sort_values(args.metric, ascending=False).head(10)
    print(df.to_string(index=False))
    return 0


def _cmd_walkforward(config: Config, args: argparse.Namespace) -> int:
    from tradingbot.backtesting.engine import BacktestEngine, BacktestSettings
    from tradingbot.backtesting.optimizer import WalkForwardAnalyzer
    from tradingbot.strategies.registry import get_strategy_class

    timeframe = Timeframe.from_string(args.timeframe)
    data = asyncio.run(
        _load_data(config, args.symbols, timeframe, args.start, args.end, args.download)
    )
    engine = BacktestEngine(
        BacktestSettings(
            initial_balance=config.backtest.initial_balance,
            commission_rate=config.backtest.commission_rate,
            slippage_rate=config.backtest.slippage_rate,
            spread_rate=config.backtest.spread_rate,
            leverage=config.backtest.leverage,
        ),
        config.risk,
        config.sizing,
    )
    analyzer = WalkForwardAnalyzer(
        engine, get_strategy_class(args.strategy), args.symbols, timeframe, metric=args.metric
    )
    result = analyzer.analyze(
        json.loads(args.grid), data, n_windows=args.windows, train_ratio=args.train_ratio
    )
    print("\n=== Walk-Forward-Fenster ===")
    for i, (train, test, params) in enumerate(result.windows, start=1):
        print(
            f"  Fenster {i}: params={params} | "
            f"Train-{args.metric}={train.best_score:.4f} | "
            f"OOS-PnL={test.report.total_pnl:.2f} ({test.report.trade_count} Trades)"
        )
    _print_report(result.oos_metrics)
    return 0


def _cmd_list_strategies(config: Config) -> int:
    from tradingbot.strategies.registry import discover_strategies

    registry = discover_strategies()
    print(f"{len(registry)} Strategien verfügbar:\n")
    for name in sorted(registry):
        cls = registry[name]
        doc = (cls.__doc__ or "").strip().splitlines()[0] if cls.__doc__ else ""
        print(f"  {name:<20} {doc}")
        if cls.default_params:
            print(f"  {'':<20} Parameter: {cls.default_params}")
    return 0


def _cmd_dashboard(config: Config, config_path: str) -> int:
    import subprocess

    app_path = Path(__file__).parent / "dashboard" / "app.py"
    cmd = [
        sys.executable, "-m", "streamlit", "run", str(app_path),
        "--server.port", str(config.dashboard.port),
        "--server.address", config.dashboard.host,
        "--", "--config", config_path,
    ]
    logger.info("Starte Dashboard: %s", " ".join(cmd))
    return subprocess.call(cmd)


def main(argv: list[str] | None = None) -> int:
    """CLI-Einstiegspunkt.

    Args:
        argv: Argumente (None = ``sys.argv[1:]``).

    Returns:
        Prozess-Exitcode.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    config = load_config(args.config)
    setup_logging(config.logging)

    match args.command:
        case "run":
            return _cmd_run(config)
        case "download":
            return _cmd_download(config, args)
        case "backtest":
            return _cmd_backtest(config, args)
        case "optimize":
            return _cmd_optimize(config, args)
        case "walkforward":
            return _cmd_walkforward(config, args)
        case "list-strategies":
            return _cmd_list_strategies(config)
        case "dashboard":
            return _cmd_dashboard(config, args.config)
    parser.error(f"Unbekanntes Kommando: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
