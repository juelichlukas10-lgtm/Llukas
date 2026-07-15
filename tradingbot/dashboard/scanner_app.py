"""Streamlit-Dashboard des Buy-the-Dip-Scanners.

Eigenständige App (getrennt vom Bot-Dashboard). Liest ausschließlich
aus der Datenbank, in die die Scanner-Engine schreibt, und lädt für den
Detail-Chart aktuelle Kursdaten des ausgewählten Symbols nach.

Start:
    python main.py scanner-dashboard
    # oder direkt:
    streamlit run tradingbot/dashboard/scanner_app.py -- --config config/config.yaml
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tradingbot.core.config import Config, load_config  # noqa: E402
from tradingbot.database.repository import Database  # noqa: E402

_STATUS_LABELS = {
    "watching": "👀 Beobachtung",
    "confirmed": "✅ Bestätigt",
    "entry": "🚀 Einstieg",
    "target_reached": "🎯 Ziel erreicht",
    "invalidated": "❌ Ungültig",
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/config.yaml")
    known, _ = parser.parse_known_args()
    return known


@st.cache_resource
def _load(config_path: str) -> tuple[Config, Database]:
    """Lädt Konfiguration und Datenbank (inkl. Secrets-Bridge für die Cloud)."""
    import os

    try:
        for key, value in dict(st.secrets).items():
            os.environ.setdefault(key, str(value))
    except st.errors.StreamlitSecretNotFoundError:
        pass
    config = load_config(config_path)
    return config, Database(url=config.database.url, echo=False)


@st.cache_data(ttl=300)
def _fetch_chart_data(symbol: str, period: str = "1y") -> pd.DataFrame:
    """Lädt Tageskerzen für den Detail-Chart (5 Minuten gecacht)."""
    import yfinance as yf

    raw = yf.download(symbol, period=period, interval="1d", auto_adjust=True, progress=False)
    if raw is None or raw.empty:
        return pd.DataFrame()
    if raw.columns.nlevels > 1:
        raw.columns = raw.columns.get_level_values(0)
    df = raw.rename(columns={c: str(c).lower() for c in raw.columns})
    return df[["open", "high", "low", "close", "volume"]].dropna()


def _paper_trading_kpi_row(db: Database, config: Config) -> None:
    """Kennzahlen-Zeile des Scanner-Paper-Depots."""
    if not config.scanner.paper_trading.enabled:
        return
    positions = db.get_scanner_positions()
    trades = db.get_scanner_trades(limit=None)
    cash = db.get_scanner_cash(default=config.scanner.paper_trading.initial_balance)
    book_value = sum(p.amount * p.entry_price for p in positions)
    equity = cash + book_value
    wins = sum(1 for t in trades if t.pnl > 0)
    total_pnl = sum(t.pnl for t in trades)

    st.subheader("💰 Paper-Trading-Depot")
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric(
        "Equity", f"${equity:,.0f}",
        f"{(equity / config.scanner.paper_trading.initial_balance - 1):+.1%}",
    )
    col2.metric("Bargeld", f"${cash:,.0f}")
    col3.metric("Offene Positionen", len(positions))
    col4.metric("Abgeschlossene Trades", len(trades), f"{wins}/{len(trades)} Gewinner" if trades else None)
    col5.metric("Gesamt-PnL", f"${total_pnl:+,.0f}")


def _paper_trading_section(db: Database, config: Config) -> None:
    """Offene Positionen und Trade-Historie des Scanner-Depots."""
    if not config.scanner.paper_trading.enabled:
        st.info("Paper-Trading ist deaktiviert (scanner.paper_trading.enabled: false).")
        return

    st.subheader("Offene Positionen")
    positions = db.get_scanner_positions()
    if not positions:
        st.info("Keine offenen Paper-Positionen.")
    else:
        rows = [
            {
                "Symbol": p.symbol,
                "Name": p.name,
                "Menge": p.amount,
                "Einstieg": p.entry_price,
                "Stop": p.stop_loss,
                "Ziel 1": p.target_1,
                "Ziel 2": p.target_2,
                "Teilverkauf erfolgt": "Ja" if p.partial_exit_done else "Nein",
                "Eröffnet": pd.Timestamp(p.opened_at).strftime("%d.%m. %H:%M"),
            }
            for p in positions
        ]
        st.dataframe(
            pd.DataFrame(rows), use_container_width=True, hide_index=True,
            column_config={
                "Einstieg": st.column_config.NumberColumn(format="%.2f"),
                "Stop": st.column_config.NumberColumn(format="%.2f"),
                "Ziel 1": st.column_config.NumberColumn(format="%.2f"),
                "Ziel 2": st.column_config.NumberColumn(format="%.2f"),
                "Menge": st.column_config.NumberColumn(format="%.2f"),
            },
        )

    st.subheader("Trade-Historie")
    trades = db.get_scanner_trades(limit=100)
    if not trades:
        st.info("Noch keine abgeschlossenen Paper-Trades.")
        return
    rows = [
        {
            "Symbol": t.symbol,
            "Menge": t.amount,
            "Einstieg": t.entry_price,
            "Ausstieg": t.exit_price,
            "PnL": t.pnl,
            "Gebühren": t.fees,
            "Grund": t.exit_reason,
            "Geschlossen": pd.Timestamp(t.closed_at).strftime("%d.%m. %H:%M"),
        }
        for t in trades
    ]
    df = pd.DataFrame(rows)
    st.dataframe(
        df.style.map(
            lambda v: "color: #00cc96" if isinstance(v, float) and v > 0 else (
                "color: #ef553b" if isinstance(v, float) and v < 0 else ""
            ),
            subset=["PnL"],
        ),
        use_container_width=True, hide_index=True, height=360,
    )


def _kpi_header(db: Database, config: Config) -> None:
    """Kennzahlen-Kopfzeile: Universum, Signale, letzter Scan."""
    status = db.get_scanner_status()
    signals = db.get_scanner_signals(active_only=True)
    entry_count = sum(1 for s in signals if s.status == "entry")

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Überwachte Aktien", status.universe_size if status else "–")
    col2.metric("Aktive Setups", len(signals))
    col3.metric("Einstiegssignale", entry_count)
    col4.metric(
        "Bester Score", f"{signals[0].score:.0f}" if signals else "–",
        signals[0].symbol if signals else None,
    )
    if status is not None:
        age_minutes = (pd.Timestamp.now(tz="UTC") - pd.Timestamp(status.last_scan_at)).total_seconds() / 60
        running = "🟢 aktiv" if status.running else "🔴 gestoppt"
        col5.metric("Letzter Scan", f"vor {age_minutes:.0f} min", running)
    else:
        col5.metric("Letzter Scan", "–", "🔴 noch kein Durchlauf")


def _ranking_table(db: Database, config: Config) -> str | None:
    """Live-Rangliste; liefert das ausgewählte Symbol für den Detail-Chart."""
    show_inactive = st.toggle("Auch ungültige/abgeschlossene Setups zeigen", value=False)
    signals = db.get_scanner_signals(active_only=not show_inactive)
    if not signals:
        st.info(
            "Noch keine Setups gefunden. Läuft der Scanner? "
            "Start: `python main.py scan` – der erste Durchlauf kann einige Minuten dauern."
        )
        return None

    rows = []
    for s in signals:
        rows.append(
            {
                "Symbol": s.symbol,
                "Name": s.name,
                "Status": _STATUS_LABELS.get(s.status, s.status),
                "Score": s.score,
                "Kurs": s.price,
                "Heute %": s.change_pct * 100,
                "Vom Hoch %": -s.drawdown_pct * 100,
                "Unterstützung": f"{s.support_type} ({s.support_distance_pct:+.1%})",
                "Trend": s.trend_strength,
                "RSI": s.rsi,
                "Volumen-Ratio": s.volume_ratio,
                "CRV": s.risk_reward,
                "Erkannt": pd.Timestamp(s.detected_at).strftime("%d.%m. %H:%M"),
            }
        )
    df = pd.DataFrame(rows)

    st.dataframe(
        df,
        use_container_width=True,
        height=420,
        hide_index=True,
        column_config={
            "Score": st.column_config.ProgressColumn(
                "Score", min_value=0, max_value=100, format="%.0f"
            ),
            "Trend": st.column_config.ProgressColumn(
                "Trend", min_value=0.0, max_value=1.0, format="%.2f"
            ),
            "Kurs": st.column_config.NumberColumn(format="%.2f"),
            "Heute %": st.column_config.NumberColumn(format="%+.2f%%"),
            "Vom Hoch %": st.column_config.NumberColumn(format="%.1f%%"),
            "RSI": st.column_config.NumberColumn(format="%.0f"),
            "Volumen-Ratio": st.column_config.NumberColumn(format="%.2f"),
            "CRV": st.column_config.NumberColumn(format="%.1f"),
        },
    )
    return st.selectbox("Setup für Detail-Chart", [s.symbol for s in signals])


def _detail_chart(db: Database, symbol: str) -> None:
    """Chart mit EMAs, Unterstützung, Einstieg, Stop und Kurszielen."""
    records = {s.symbol: s for s in db.get_scanner_signals(active_only=False)}
    signal = records.get(symbol)
    if signal is None:
        st.warning(f"Kein gespeichertes Setup für {symbol}.")
        return

    df = _fetch_chart_data(symbol)
    if df.empty:
        st.error(f"Kursdaten für {symbol} konnten nicht geladen werden.")
        return
    df = df.tail(180)

    ema20 = df["close"].ewm(span=20, adjust=False).mean()
    ema50 = df["close"].ewm(span=50, adjust=False).mean()
    ema200 = df["close"].ewm(span=200, adjust=False).mean()

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True, row_heights=[0.75, 0.25], vertical_spacing=0.03
    )
    fig.add_trace(
        go.Candlestick(
            x=df.index, open=df["open"], high=df["high"], low=df["low"], close=df["close"],
            name="Kurs",
        ),
        row=1, col=1,
    )
    for series, label, color in (
        (ema20, "EMA20", "#636efa"),
        (ema50, "EMA50", "#f0a15a"),
        (ema200, "EMA200", "#ab63fa"),
    ):
        fig.add_trace(
            go.Scatter(x=df.index, y=series, name=label, line={"width": 1.3, "color": color}),
            row=1, col=1,
        )

    # Horizontale Level: Unterstützung, Einstieg, Stop, Ziele, Bezugshoch.
    levels = [
        (signal.support_level, f"Unterstützung ({signal.support_type})", "#f7c948", "dot"),
        (signal.entry_price, "Einstieg", "#00cc96", "dash"),
        (signal.stop_loss, "Stop-Loss", "#ef553b", "dash"),
        (signal.target_1, "Ziel 1", "#19c37d", "dot"),
        (signal.target_2, "Ziel 2", "#0e8a5f", "dot"),
        (signal.recent_high, "Bezugshoch", "#8890a3", "dot"),
    ]
    for level, label, color, dash in levels:
        if level and level > 0:
            fig.add_hline(
                y=level, line={"color": color, "dash": dash, "width": 1.2},
                annotation_text=label, annotation_position="right",
                row=1, col=1,
            )

    volume_colors = [
        "#26a69a" if c >= o else "#ef5350" for o, c in zip(df["open"], df["close"])
    ]
    fig.add_trace(
        go.Bar(x=df.index, y=df["volume"], name="Volumen", marker={"color": volume_colors}),
        row=2, col=1,
    )
    fig.update_layout(
        height=640, xaxis_rangeslider_visible=False,
        margin={"t": 30, "b": 10}, legend={"orientation": "h", "y": 1.06},
        title=f"{signal.name} ({symbol}) – {_STATUS_LABELS.get(signal.status, signal.status)}",
    )
    st.plotly_chart(fig, use_container_width=True)

    # Score-Aufschlüsselung.
    if signal.score_breakdown:
        st.subheader("Score-Aufschlüsselung")
        breakdown = pd.DataFrame(
            {"Komponente": list(signal.score_breakdown.keys()),
             "Punkte": list(signal.score_breakdown.values())}
        ).sort_values("Punkte", ascending=True)
        bar = go.Figure(
            go.Bar(x=breakdown["Punkte"], y=breakdown["Komponente"], orientation="h",
                   marker={"color": "#636efa"})
        )
        bar.update_layout(height=280, margin={"t": 10, "b": 10})
        st.plotly_chart(bar, use_container_width=True)


def main() -> None:
    """Baut die Scanner-Dashboard-Seite auf."""
    st.set_page_config(page_title="Buy-the-Dip-Scanner", page_icon="🔍", layout="wide")
    args = _parse_args()
    config, db = _load(args.config)

    st.title("🔍 Buy-the-Dip-Scanner")
    st.caption(
        f"Universen: {', '.join(config.scanner.universes)} | "
        f"Zyklus: alle {config.scanner.interval_seconds / 60:.0f} min | "
        f"Mindest-Score: {config.scanner.filters.min_score:.0f}"
    )

    with st.sidebar:
        st.header("Steuerung")
        if st.button("🔄 Jetzt aktualisieren"):
            _fetch_chart_data.clear()
            st.rerun()
        auto = st.checkbox("Auto-Refresh", value=True)
        interval = st.slider("Refresh-Intervall (s)", 10, 120, 30)
        st.divider()
        st.caption(
            "Der Scanner läuft als eigener Prozess: `python main.py scan`. "
            "Dieses Dashboard zeigt dessen Ergebnisse live an."
        )

    _kpi_header(db, config)
    st.divider()
    _paper_trading_kpi_row(db, config)
    st.divider()

    tab_signals, tab_paper = st.tabs(["📈 Setups & Chart", "💰 Paper-Trading"])
    with tab_signals:
        selected = _ranking_table(db, config)
        if selected:
            st.divider()
            _detail_chart(db, selected)
    with tab_paper:
        _paper_trading_section(db, config)

    if auto:
        time.sleep(interval)
        st.rerun()


main()
