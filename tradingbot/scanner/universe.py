"""Ticker-Universen des Marktscanners.

Eingebaute Universen (statische Snapshots, Stand ca. 2025 – Indexänderungen
werden vom Datenprovider toleriert: Ticker ohne Kursdaten werden beim Scan
einfach übersprungen):

    * ``dow_jones``   – Dow Jones Industrial Average (30 Werte)
    * ``nasdaq_100``  – Nasdaq-100
    * ``sp500``       – S&P 500
    * ``eu_large``    – große europäische Aktien (Yahoo-Suffixe .DE/.PA/.AS/...)
    * ``international`` – große internationale Werte (ADRs/US-Listings)

Erweiterbar über die Konfiguration (``scanner.custom_tickers`` und
``scanner.universe_csv`` – eine CSV mit Spalte ``symbol`` und optional
``name``).
"""

from __future__ import annotations

import csv
from pathlib import Path

from tradingbot.core.exceptions import ConfigError
from tradingbot.core.logging import get_logger

logger = get_logger(__name__)

DOW_JONES: dict[str, str] = {
    "AAPL": "Apple", "AMGN": "Amgen", "AMZN": "Amazon", "AXP": "American Express",
    "BA": "Boeing", "CAT": "Caterpillar", "CRM": "Salesforce", "CSCO": "Cisco",
    "CVX": "Chevron", "DIS": "Walt Disney", "GS": "Goldman Sachs", "HD": "Home Depot",
    "HON": "Honeywell", "IBM": "IBM", "JNJ": "Johnson & Johnson", "JPM": "JPMorgan Chase",
    "KO": "Coca-Cola", "MCD": "McDonald's", "MMM": "3M", "MRK": "Merck",
    "MSFT": "Microsoft", "NKE": "Nike", "NVDA": "NVIDIA", "PG": "Procter & Gamble",
    "SHW": "Sherwin-Williams", "TRV": "Travelers", "UNH": "UnitedHealth",
    "V": "Visa", "VZ": "Verizon", "WMT": "Walmart",
}

NASDAQ_100: dict[str, str] = {
    "AAPL": "Apple", "ABNB": "Airbnb", "ADBE": "Adobe", "ADI": "Analog Devices",
    "ADP": "ADP", "ADSK": "Autodesk", "AEP": "American Electric Power",
    "AMAT": "Applied Materials", "AMD": "AMD", "AMGN": "Amgen", "AMZN": "Amazon",
    "ANSS": "Ansys", "APP": "AppLovin", "ARM": "Arm Holdings", "ASML": "ASML",
    "AVGO": "Broadcom", "AXON": "Axon Enterprise", "AZN": "AstraZeneca",
    "BIIB": "Biogen", "BKNG": "Booking Holdings", "BKR": "Baker Hughes",
    "CCEP": "Coca-Cola Europacific", "CDNS": "Cadence Design", "CDW": "CDW",
    "CEG": "Constellation Energy", "CHTR": "Charter Communications",
    "CMCSA": "Comcast", "COST": "Costco", "CPRT": "Copart", "CRWD": "CrowdStrike",
    "CSCO": "Cisco", "CSGP": "CoStar Group", "CSX": "CSX", "CTAS": "Cintas",
    "CTSH": "Cognizant", "DASH": "DoorDash", "DDOG": "Datadog", "DXCM": "DexCom",
    "EA": "Electronic Arts", "EXC": "Exelon", "FANG": "Diamondback Energy",
    "FAST": "Fastenal", "FTNT": "Fortinet", "GEHC": "GE HealthCare",
    "GFS": "GlobalFoundries", "GILD": "Gilead Sciences", "GOOG": "Alphabet (C)",
    "GOOGL": "Alphabet (A)", "HON": "Honeywell", "IDXX": "IDEXX Labs",
    "INTC": "Intel", "INTU": "Intuit", "ISRG": "Intuitive Surgical",
    "KDP": "Keurig Dr Pepper", "KHC": "Kraft Heinz", "KLAC": "KLA",
    "LIN": "Linde", "LRCX": "Lam Research", "LULU": "Lululemon",
    "MAR": "Marriott", "MCHP": "Microchip", "MDLZ": "Mondelez",
    "MELI": "MercadoLibre", "META": "Meta Platforms", "MNST": "Monster Beverage",
    "MRVL": "Marvell", "MSFT": "Microsoft", "MU": "Micron", "NFLX": "Netflix",
    "NVDA": "NVIDIA", "NXPI": "NXP Semiconductors", "ODFL": "Old Dominion",
    "ON": "ON Semiconductor", "ORLY": "O'Reilly Automotive", "PANW": "Palo Alto Networks",
    "PAYX": "Paychex", "PCAR": "PACCAR", "PDD": "PDD Holdings", "PEP": "PepsiCo",
    "PLTR": "Palantir", "PYPL": "PayPal", "QCOM": "Qualcomm", "REGN": "Regeneron",
    "ROP": "Roper Technologies", "ROST": "Ross Stores", "SBUX": "Starbucks",
    "SNPS": "Synopsys", "TEAM": "Atlassian", "TMUS": "T-Mobile US",
    "TSLA": "Tesla", "TTD": "The Trade Desk", "TTWO": "Take-Two Interactive",
    "TXN": "Texas Instruments", "VRSK": "Verisk", "VRTX": "Vertex Pharma",
    "WBD": "Warner Bros. Discovery", "WDAY": "Workday", "XEL": "Xcel Energy",
    "ZS": "Zscaler",
}

#: S&P 500 – Ticker-Snapshot. Namen werden, wo nicht in anderen Universen
#: vorhanden, durch das Symbol ersetzt (rein kosmetisch).
SP500_TICKERS: list[str] = [
    "A", "AAPL", "ABBV", "ABNB", "ABT", "ACGL", "ACN", "ADBE", "ADI", "ADM",
    "ADP", "ADSK", "AEE", "AEP", "AES", "AFL", "AIG", "AIZ", "AJG", "AKAM",
    "ALB", "ALGN", "ALL", "ALLE", "AMAT", "AMCR", "AMD", "AME", "AMGN", "AMP",
    "AMT", "AMZN", "ANET", "ANSS", "AON", "AOS", "APA", "APD", "APH", "APTV",
    "ARE", "ATO", "AVB", "AVGO", "AVY", "AWK", "AXON", "AXP", "AZO", "BA",
    "BAC", "BALL", "BAX", "BBY", "BDX", "BEN", "BF-B", "BG", "BIIB", "BK",
    "BKNG", "BKR", "BLDR", "BLK", "BMY", "BR", "BRK-B", "BRO", "BSX", "BX",
    "BXP", "C", "CAG", "CAH", "CARR", "CAT", "CB", "CBOE", "CBRE", "CCI",
    "CCL", "CDNS", "CDW", "CE", "CEG", "CF", "CFG", "CHD", "CHRW", "CHTR",
    "CI", "CINF", "CL", "CLX", "CMCSA", "CME", "CMG", "CMI", "CMS", "CNC",
    "CNP", "COF", "COO", "COP", "COR", "COST", "CPAY", "CPB", "CPRT", "CPT",
    "CRL", "CRM", "CRWD", "CSCO", "CSGP", "CSX", "CTAS", "CTRA", "CTSH", "CTVA",
    "CVS", "CVX", "CZR", "D", "DAL", "DAY", "DD", "DE", "DECK", "DFS",
    "DG", "DGX", "DHI", "DHR", "DIS", "DLR", "DLTR", "DOC", "DOV", "DOW",
    "DPZ", "DRI", "DTE", "DUK", "DVA", "DVN", "DXCM", "EA", "EBAY", "ECL",
    "ED", "EFX", "EG", "EIX", "EL", "ELV", "EMN", "EMR", "ENPH", "EOG",
    "EPAM", "EQIX", "EQR", "EQT", "ERIE", "ES", "ESS", "ETN", "ETR", "EVRG",
    "EW", "EXC", "EXPD", "EXPE", "EXR", "F", "FANG", "FAST", "FCX", "FDS",
    "FDX", "FE", "FFIV", "FI", "FICO", "FIS", "FITB", "FMC", "FOX", "FOXA",
    "FRT", "FSLR", "FTNT", "FTV", "GD", "GDDY", "GE", "GEHC", "GEN", "GEV",
    "GILD", "GIS", "GL", "GLW", "GM", "GNRC", "GOOG", "GOOGL", "GPC", "GPN",
    "GRMN", "GS", "GWW", "HAL", "HAS", "HBAN", "HCA", "HD", "HES", "HIG",
    "HII", "HLT", "HOLX", "HON", "HPE", "HPQ", "HRL", "HSIC", "HST", "HSY",
    "HUBB", "HUM", "HWM", "IBM", "ICE", "IDXX", "IEX", "IFF", "INCY", "INTC",
    "INTU", "INVH", "IP", "IPG", "IQV", "IR", "IRM", "ISRG", "IT", "ITW",
    "IVZ", "J", "JBHT", "JBL", "JCI", "JKHY", "JNJ", "JNPR", "JPM", "K",
    "KDP", "KEY", "KEYS", "KHC", "KIM", "KKR", "KLAC", "KMB", "KMI", "KMX",
    "KO", "KR", "KVUE", "L", "LDOS", "LEN", "LH", "LHX", "LII", "LIN",
    "LKQ", "LLY", "LMT", "LNT", "LOW", "LRCX", "LULU", "LUV", "LVS", "LW",
    "LYB", "LYV", "MA", "MAA", "MAR", "MAS", "MCD", "MCHP", "MCK", "MCO",
    "MDLZ", "MDT", "MET", "META", "MGM", "MHK", "MKC", "MKTX", "MLM", "MMC",
    "MMM", "MNST", "MO", "MOH", "MOS", "MPC", "MPWR", "MRK", "MRNA", "MS",
    "MSCI", "MSFT", "MSI", "MTB", "MTCH", "MTD", "MU", "NCLH", "NDAQ", "NDSN",
    "NEE", "NEM", "NFLX", "NI", "NKE", "NOC", "NOW", "NRG", "NSC", "NTAP",
    "NTRS", "NUE", "NVDA", "NVR", "NWS", "NWSA", "NXPI", "O", "ODFL", "OKE",
    "OMC", "ON", "ORCL", "ORLY", "OTIS", "OXY", "PANW", "PARA", "PAYC", "PAYX",
    "PCAR", "PCG", "PEG", "PEP", "PFE", "PFG", "PG", "PGR", "PH", "PHM",
    "PKG", "PLD", "PLTR", "PM", "PNC", "PNR", "PNW", "PODD", "POOL", "PPG",
    "PPL", "PRU", "PSA", "PSX", "PTC", "PWR", "PYPL", "QCOM", "RCL", "REG",
    "REGN", "RF", "RJF", "RL", "RMD", "ROK", "ROL", "ROP", "ROST", "RSG",
    "RTX", "RVTY", "SBAC", "SBUX", "SCHW", "SHW", "SJM", "SLB", "SMCI", "SNA",
    "SNPS", "SO", "SOLV", "SPG", "SPGI", "SRE", "STE", "STLD", "STT", "STX",
    "STZ", "SWK", "SWKS", "SYF", "SYK", "SYY", "T", "TAP", "TDG", "TDY",
    "TECH", "TEL", "TER", "TFC", "TGT", "TJX", "TMO", "TMUS", "TPR", "TRGP",
    "TRMB", "TROW", "TRV", "TSCO", "TSLA", "TSN", "TT", "TTWO", "TXN", "TXT",
    "TYL", "UAL", "UBER", "UDR", "UHS", "ULTA", "UNH", "UNP", "UPS", "URI",
    "USB", "V", "VICI", "VLO", "VLTO", "VMC", "VRSK", "VRSN", "VRTX", "VST",
    "VTR", "VTRS", "VZ", "WAB", "WAT", "WBA", "WBD", "WDC", "WEC", "WELL",
    "WFC", "WM", "WMB", "WMT", "WRB", "WSM", "WST", "WTW", "WY", "WYNN",
    "XEL", "XOM", "XYL", "YUM", "ZBH", "ZBRA", "ZTS",
]

EU_LARGE: dict[str, str] = {
    "SAP.DE": "SAP", "SIE.DE": "Siemens", "ALV.DE": "Allianz", "DTE.DE": "Deutsche Telekom",
    "AIR.DE": "Airbus", "MBG.DE": "Mercedes-Benz", "BMW.DE": "BMW", "BAS.DE": "BASF",
    "BAYN.DE": "Bayer", "IFX.DE": "Infineon", "MUV2.DE": "Munich Re", "RHM.DE": "Rheinmetall",
    "DBK.DE": "Deutsche Bank", "ADS.DE": "Adidas", "VOW3.DE": "Volkswagen",
    "ASML.AS": "ASML", "ADYEN.AS": "Adyen", "INGA.AS": "ING", "PHIA.AS": "Philips",
    "HEIA.AS": "Heineken", "MC.PA": "LVMH", "OR.PA": "L'Oréal", "TTE.PA": "TotalEnergies",
    "SAN.PA": "Sanofi", "AI.PA": "Air Liquide", "SU.PA": "Schneider Electric",
    "BNP.PA": "BNP Paribas", "CS.PA": "AXA", "RMS.PA": "Hermès", "EL.PA": "EssilorLuxottica",
    "NESN.SW": "Nestlé", "NOVN.SW": "Novartis", "ROG.SW": "Roche", "UBSG.SW": "UBS",
    "ZURN.SW": "Zurich Insurance", "ABBN.SW": "ABB", "AZN.L": "AstraZeneca",
    "SHEL.L": "Shell", "HSBA.L": "HSBC", "ULVR.L": "Unilever", "BP.L": "BP",
    "GSK.L": "GSK", "RIO.L": "Rio Tinto", "REL.L": "RELX", "NOVO-B.CO": "Novo Nordisk",
    "ISP.MI": "Intesa Sanpaolo", "UCG.MI": "UniCredit", "ENEL.MI": "Enel",
    "SAN.MC": "Banco Santander", "ITX.MC": "Inditex", "IBE.MC": "Iberdrola",
}

INTERNATIONAL: dict[str, str] = {
    "TSM": "Taiwan Semiconductor (ADR)", "BABA": "Alibaba (ADR)", "TM": "Toyota (ADR)",
    "SONY": "Sony (ADR)", "NVO": "Novo Nordisk (ADR)", "SHOP": "Shopify",
    "SE": "Sea Limited (ADR)", "JD": "JD.com (ADR)", "PDD": "PDD Holdings (ADR)",
    "BIDU": "Baidu (ADR)", "NTES": "NetEase (ADR)", "MUFG": "Mitsubishi UFJ (ADR)",
    "SMFG": "Sumitomo Mitsui (ADR)", "HDB": "HDFC Bank (ADR)", "IBN": "ICICI Bank (ADR)",
    "INFY": "Infosys (ADR)", "RIO": "Rio Tinto (ADR)", "BHP": "BHP (ADR)",
    "VALE": "Vale (ADR)", "PBR": "Petrobras (ADR)", "MELI": "MercadoLibre",
    "NU": "Nu Holdings", "GRAB": "Grab Holdings", "CPNG": "Coupang", "ARM": "Arm (ADR)",
}

#: Registry der eingebauten Universen.
BUILTIN_UNIVERSES: dict[str, dict[str, str]] = {
    "dow_jones": DOW_JONES,
    "nasdaq_100": NASDAQ_100,
    "sp500": {t: (NASDAQ_100.get(t) or DOW_JONES.get(t) or t) for t in SP500_TICKERS},
    "eu_large": EU_LARGE,
    "international": INTERNATIONAL,
}


def load_universe(
    universes: list[str],
    custom_tickers: list[str] | None = None,
    csv_path: str | Path | None = None,
) -> dict[str, str]:
    """Baut das Gesamt-Universum aus eingebauten und eigenen Quellen.

    Args:
        universes: Namen eingebauter Universen (siehe ``BUILTIN_UNIVERSES``).
        custom_tickers: Zusätzliche einzelne Ticker.
        csv_path: Optionale CSV mit Spalte ``symbol`` (und optional ``name``),
            z. B. für Russell 2000 oder komplette US-Listen des Datenanbieters.

    Returns:
        Mapping ``symbol -> name`` (dedupliziert, alphabetisch sortiert).

    Raises:
        ConfigError: Bei unbekanntem Universum oder fehlerhafter CSV.
    """
    result: dict[str, str] = {}

    for name in universes:
        key = name.lower()
        if key not in BUILTIN_UNIVERSES:
            raise ConfigError(
                f"Unbekanntes Universum '{name}'. Verfügbar: {sorted(BUILTIN_UNIVERSES)}"
            )
        result.update(BUILTIN_UNIVERSES[key])

    for ticker in custom_tickers or []:
        symbol = ticker.strip().upper()
        if symbol:
            result.setdefault(symbol, symbol)

    if csv_path is not None:
        path = Path(csv_path)
        if not path.exists():
            raise ConfigError(f"Universe-CSV nicht gefunden: {path.resolve()}")
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None or "symbol" not in [f.lower() for f in reader.fieldnames]:
                raise ConfigError(f"Universe-CSV {path} benötigt eine Spalte 'symbol'")
            symbol_col = next(f for f in reader.fieldnames if f.lower() == "symbol")
            name_col = next((f for f in reader.fieldnames if f.lower() == "name"), None)
            for row in reader:
                symbol = (row.get(symbol_col) or "").strip().upper()
                if symbol:
                    result.setdefault(symbol, (row.get(name_col) or symbol).strip() if name_col else symbol)

    ordered = dict(sorted(result.items()))
    logger.info("Universum geladen: %d Symbole aus %s", len(ordered), universes)
    return ordered
