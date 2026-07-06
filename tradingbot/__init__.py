"""TradingBot – professioneller, modularer algorithmischer Trading-Bot.

Pakete:
    core        – Konfiguration, Logging, Domänenmodelle, Event-Bus, Engine
    exchange    – einheitliche Exchange-Abstraktion (CCXT, Paper-Trading)
    data        – Live- und historische Marktdaten
    database    – Persistenz (SQLite/PostgreSQL via SQLAlchemy)
    strategies  – Plugin-System und Strategie-Implementierungen
    risk        – Risiko-Management und Positionsgrößen
    execution   – Orderausführung und -überwachung
    backtesting – Backtest-Engine und Parameter-Optimierung
    analytics   – Indikatoren und Performance-Kennzahlen
    monitoring  – Benachrichtigungen und Health-Checks
    dashboard   – Streamlit-Dashboard
"""

__version__ = "1.0.0"
