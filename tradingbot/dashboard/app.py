"""Streamlit-Dashboard: Live-Übersicht über Trades, Performance und Logs.

Liest ausschließlich aus der Bot-Datenbank und dem lokalen Datenspeicher –
das Dashboard läuft damit unabhängig vom Bot-Prozess und kann jederzeit
gestartet oder geschlossen werden.

Start:
    python main.py dashboard
    # oder direkt:
    streamlit run tradingbot/dashboard/app.py -- --config config/config.yaml
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

# Projektwurzel importierbar machen, wenn Streamlit die Datei direkt lädt.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tradingbot.core.config import Config, load_config  # noqa: E402
from tradingbot.core.enums import Timeframe  # noqa: E402
from tradingbot.data.storage import CandleStorage  # noqa: E402
from tradingbot.database.repository import Database  # noqa: E402


def _parse_args() -> argparse.Namespace:
    """Parst die hinter ``--`` übergebenen Streamlit-Argumente."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/config.yaml")
    known, _ = parser.parse_known_args()
    return known


@st.cache_resource
def _load(config_path: str) -> tuple[Config, Database, CandleStorage]:
    """Lädt Konfiguration, Datenbank und Datenspeicher (einmalig gecacht)."""
    config = load_config(config_path)
    database = Database(url=config.database.url, echo=False)
    storage = CandleStorage(config.data.storage_dir)
    return config, database, storage


def _records_to_df(records: list[object]) -> pd.DataFrame:
    """Konvertiert SQLAlchemy-Records in einen DataFrame."""
    if not records:
        return pd.DataFrame()
    rows = []
    for record in records:
        row = {
            k: v for k, v in vars(record).items() if not k.startswith("_")
        }
        rows.append(row)
    return pd.DataFrame(rows)


def _kpi_row(config: Config, db: Database) -> None:
    """Oberste KPI-Zeile: Equity, PnL, Positionen, Trades."""
    perf = db.get_performance_history(limit=100_000)
    trades = db.get_trades()
    positions = db.get_positions()

    equity = perf[-1].equity if perf else config.paper.initial_balance
    total_pnl = sum(t.pnl for t in trades)
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    today_pnl = sum(t.pnl for t in trades if t.closed_at.replace(tzinfo=timezone.utc) >= today)
    wins = sum(1 for t in trades if t.pnl > 0)
    win_rate = wins / len(trades) if trades else 0.0

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Kontostand (Equity)", f"{equity:,.2f}")
    col2.metric("Gesamt-PnL", f"{total_pnl:+,.2f}")
    col3.metric("PnL heute", f"{today_pnl:+,.2f}")
    col4.metric("Offene Positionen", len(positions))
    col5.metric("Trefferquote", f"{win_rate:.1%}" if trades else "–")


def _equity_tab(db: Database) -> None:
    """Equity-Kurve und Drawdown."""
    perf = db.get_performance_history(limit=100_000)
    if not perf:
        st.info("Noch keine Performance-Daten vorhanden. Bot laufen lassen oder Backtest ausführen.")
        return
    df = pd.DataFrame(
        {"timestamp": [p.timestamp for p in perf], "equity": [p.equity for p in perf]}
    ).set_index("timestamp")
    drawdown = 1.0 - df["equity"] / df["equity"].cummax()

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True, row_heights=[0.7, 0.3], vertical_spacing=0.05,
        subplot_titles=("Equity-Kurve", "Drawdown"),
    )
    fig.add_trace(
        go.Scatter(x=df.index, y=df["equity"], name="Equity", line={"color": "#00cc96"}),
        row=1, col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=drawdown.index, y=-drawdown * 100, name="Drawdown %",
            fill="tozeroy", line={"color": "#ef553b"},
        ),
        row=2, col=1,
    )
    fig.update_layout(height=560, showlegend=False, margin={"t": 40, "b": 20})
    fig.update_yaxes(title_text="Equity", row=1, col=1)
    fig.update_yaxes(title_text="DD %", row=2, col=1)
    st.plotly_chart(fig, use_container_width=True)


def _trades_tab(db: Database) -> None:
    """Trade-Historie mit Kennzahlen und Heatmap."""
    trades = db.get_trades(limit=1000)
    if not trades:
        st.info("Noch keine abgeschlossenen Trades.")
        return
    df = _records_to_df(trades)
    df = df[
        ["closed_at", "symbol", "side", "strategy", "amount", "entry_price",
         "exit_price", "pnl", "fees", "exit_reason"]
    ].sort_values("closed_at", ascending=False)

    st.dataframe(
        df.style.map(
            lambda v: "color: #00cc96" if isinstance(v, float) and v > 0 else (
                "color: #ef553b" if isinstance(v, float) and v < 0 else ""
            ),
            subset=["pnl"],
        ),
        use_container_width=True,
        height=380,
    )

    # PnL-Heatmap: Wochentag × Stunde.
    st.subheader("PnL-Heatmap (Wochentag × Stunde, UTC)")
    heat = df.copy()
    heat["closed_at"] = pd.to_datetime(heat["closed_at"], utc=True)
    heat["weekday"] = heat["closed_at"].dt.day_name()
    heat["hour"] = heat["closed_at"].dt.hour
    day_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    pivot = (
        heat.pivot_table(index="weekday", columns="hour", values="pnl", aggfunc="sum")
        .reindex(day_order)
    )
    fig = go.Figure(
        data=go.Heatmap(
            z=pivot.values, x=pivot.columns, y=pivot.index,
            colorscale="RdYlGn", zmid=0,
        )
    )
    fig.update_layout(height=320, margin={"t": 10, "b": 10})
    st.plotly_chart(fig, use_container_width=True)


def _chart_tab(config: Config, db: Database, storage: CandleStorage) -> None:
    """Candlestick-Chart mit eingezeichneten Trades."""
    datasets = storage.list_datasets()
    if not datasets:
        st.info(
            "Keine lokalen Kerzendaten. Mit "
            "`python main.py download --symbol BTC/USDT --timeframe 1h --start 2024-01-01` laden."
        )
        return

    labels = [f"{d['exchange']} | {d['symbol']} | {d['timeframe']}" for d in datasets]
    choice = st.selectbox("Datensatz", labels)
    dataset = datasets[labels.index(choice)]
    timeframe = Timeframe.from_string(dataset["timeframe"])

    df = storage.load(dataset["exchange"], dataset["symbol"], timeframe)
    max_candles = st.slider("Anzahl Kerzen", 100, min(5000, len(df)), min(500, len(df)))
    df = df.tail(max_candles)

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True, row_heights=[0.75, 0.25], vertical_spacing=0.03
    )
    fig.add_trace(
        go.Candlestick(
            x=df.index, open=df["open"], high=df["high"], low=df["low"], close=df["close"],
            name="OHLC",
        ),
        row=1, col=1,
    )
    fig.add_trace(
        go.Bar(x=df.index, y=df["volume"], name="Volumen", marker={"color": "#636efa"}),
        row=2, col=1,
    )

    # Trades des Symbols einzeichnen.
    trades = db.get_trades(symbol=dataset["symbol"], limit=500)
    if trades:
        entries_x = [t.opened_at for t in trades]
        entries_y = [t.entry_price for t in trades]
        exits_x = [t.closed_at for t in trades]
        exits_y = [t.exit_price for t in trades]
        fig.add_trace(
            go.Scatter(
                x=entries_x, y=entries_y, mode="markers", name="Einstieg",
                marker={"symbol": "triangle-up", "size": 11, "color": "#00cc96"},
            ),
            row=1, col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=exits_x, y=exits_y, mode="markers", name="Ausstieg",
                marker={"symbol": "triangle-down", "size": 11, "color": "#ef553b"},
            ),
            row=1, col=1,
        )

    fig.update_layout(
        height=620, xaxis_rangeslider_visible=False, margin={"t": 20, "b": 20}
    )
    st.plotly_chart(fig, use_container_width=True)


def _positions_orders_tab(db: Database) -> None:
    """Offene Positionen und Order-Historie."""
    st.subheader("Offene Positionen")
    positions = _records_to_df(db.get_positions())
    if positions.empty:
        st.info("Keine offenen Positionen.")
    else:
        st.dataframe(positions, use_container_width=True)

    st.subheader("Letzte Orders")
    orders = _records_to_df(db.get_orders(limit=200))
    if orders.empty:
        st.info("Noch keine Orders.")
    else:
        st.dataframe(
            orders[
                ["created_at", "symbol", "side", "type", "amount", "price",
                 "status", "filled", "average_price", "strategy"]
            ].sort_values("created_at", ascending=False),
            use_container_width=True,
            height=320,
        )


def _strategies_backtests_tab(db: Database) -> None:
    """Konfigurierte Strategien und Backtest-Historie."""
    st.subheader("Strategien")
    strategies = db.get_strategies()
    if not strategies:
        st.info("Keine Strategien in der Datenbank.")
    else:
        rows = [
            {
                "Name": s.name,
                "Timeframe": s.timeframe,
                "Symbole": ", ".join(s.symbols),
                "Parameter": str(s.params),
                "Aktiv": bool(s.enabled),
                "Aktualisiert": s.updated_at,
            }
            for s in strategies
        ]
        st.dataframe(pd.DataFrame(rows), use_container_width=True)

    st.subheader("Backtests")
    backtests = db.get_backtests(limit=50)
    if not backtests:
        st.info("Noch keine Backtests gespeichert.")
        return
    rows = []
    for b in backtests:
        row = {
            "Strategie": b.strategy,
            "Symbole": ", ".join(b.symbols),
            "Timeframe": b.timeframe,
            "Zeitraum": f"{b.start:%Y-%m-%d} – {b.end:%Y-%m-%d}",
            "Start-Kapital": b.initial_balance,
            "End-Equity": round(b.final_equity, 2),
        }
        for key in ("total_return", "sharpe_ratio", "max_drawdown", "win_rate", "trade_count"):
            row[key] = b.metrics.get(key)
        rows.append(row)
    st.dataframe(pd.DataFrame(rows), use_container_width=True)


def _logs_tab(config: Config, db: Database) -> None:
    """Fehlerlogs aus der Datenbank und aktuelle Logdatei."""
    st.subheader("Fehlerlogs (Datenbank)")
    errors = db.get_error_logs(limit=100)
    if not errors:
        st.success("Keine Fehler protokolliert.")
    else:
        for error in errors:
            with st.expander(f"{error.timestamp:%Y-%m-%d %H:%M:%S} | {error.level} | {error.message[:80]}"):
                st.text(error.message)
                if error.traceback:
                    st.code(error.traceback)

    st.subheader("Aktuelle Logdatei")
    log_path = Path(config.logging.dir) / config.logging.file_name
    if log_path.exists():
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        st.code("\n".join(lines[-200:]), language="text")
    else:
        st.info(f"Logdatei {log_path} existiert noch nicht.")


def main() -> None:
    """Baut die Dashboard-Seite auf."""
    st.set_page_config(page_title="TradingBot Dashboard", page_icon="📈", layout="wide")
    args = _parse_args()
    config, db, storage = _load(args.config)

    st.title("📈 TradingBot Dashboard")
    st.caption(
        f"Modus: **{config.trading.mode.value.upper()}** | "
        f"Börse: **{config.trading.exchange}** | "
        f"Symbole: {', '.join(config.trading.symbols)}"
    )

    with st.sidebar:
        st.header("Einstellungen")
        st.text(f"Konfiguration: {args.config}")
        st.text(f"Datenbank: {config.database.url.split('/')[-1]}")
        if st.button("🔄 Aktualisieren"):
            st.cache_resource.clear()
            st.rerun()
        auto_refresh = st.checkbox("Auto-Refresh", value=False)
        if auto_refresh:
            import time

            time.sleep(config.dashboard.refresh_seconds)
            st.rerun()

    _kpi_row(config, db)
    st.divider()

    tabs = st.tabs(
        ["📊 Performance", "💹 Chart", "📜 Trades", "📌 Positionen & Orders",
         "🧠 Strategien & Backtests", "🪵 Logs"]
    )
    with tabs[0]:
        _equity_tab(db)
    with tabs[1]:
        _chart_tab(config, db, storage)
    with tabs[2]:
        _trades_tab(db)
    with tabs[3]:
        _positions_orders_tab(db)
    with tabs[4]:
        _strategies_backtests_tab(db)
    with tabs[5]:
        _logs_tab(config, db)


main()
