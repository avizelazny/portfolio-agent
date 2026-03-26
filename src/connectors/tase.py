"""
TASE Market Data Connector — Yahoo Finance Backend
====================================================
Fetches live Israeli stock market data using Yahoo Finance (yfinance).
TASE symbols on Yahoo Finance use the ".TA" suffix (e.g. TEVA.TA, NICE.TA).

No API key required. Works out of the box.

Switch between MOCK and LIVE via environment variable:
  TASE_MOCK=true   → deterministic mock data (default, for dev/testing)
  TASE_MOCK=false  → live Yahoo Finance data (production)

When TASE DataCloud API access is approved, swap _fetch_live_quote()
and _fetch_live_ohlcv() to use the official TASE API instead.
"""

import logging
import os
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
MOCK_MODE = os.getenv("TASE_MOCK", "true").lower() == "true"
_YF_SUFFIX = ".TA"  # Yahoo Finance suffix for TASE-listed stocks

# ── Data classes ──────────────────────────────────────────────────────────────


@dataclass
class Quote:
    """Live or mock quote for a single TASE-listed security."""

    symbol: str
    name: str
    price: float  # NIS — last traded price
    change: float  # NIS vs previous close
    change_pct: float  # % vs previous close
    volume: int  # shares traded today
    avg_volume_20d: int  # 20-day average volume
    open: float
    high: float
    low: float
    prev_close: float
    week52_high: float
    week52_low: float
    market_cap: float  # NIS millions
    pe_ratio: float
    sector: str
    as_of: str


@dataclass
class OHLCV:
    """Single OHLCV bar for a security."""

    symbol: str
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass
class IndexSnapshot:
    """Point-in-time snapshot of a TASE index."""

    name: str
    value: float
    change: float
    change_pct: float
    as_of: str


# ── Tracked universe ──────────────────────────────────────────────────────────
# symbol: (display_name, base_price_NIS, sector, pe, market_cap_M_NIS)
_UNIVERSE: dict[str, tuple] = {
    "TEVA":  ("Teva Pharmaceutical",       41.50, "Healthcare",    12.5, 47200),
    "NICE":  ("NICE Systems",             435.00, "Technology",    28.3, 11800),
    "CHKP":  ("Check Point Software",     545.00, "Technology",    18.7, 21500),
    "LUMI":  ("Bank Leumi",                24.10, "Financials",     8.2, 32100),
    "HAPO":  ("Bank Hapoalim",             31.50, "Financials",     9.1, 42300),
    "ICL":   ("ICL Group",                 17.20, "Materials",     11.4, 22100),
    "ESLT":  ("Elbit Systems",            612.00, "Defense",       22.1,  9800),
    "MGDL":  ("Migdal Insurance",          10.85, "Financials",    10.3,  4200),
    "AMOT":  ("Amot Investments",          27.10, "Real Estate",   15.6,  6800),
    "BEZQ":  ("Bezeq",                      6.42, "Telecom",       14.2, 10100),
    "DLSN":  ("Delek Group",              975.00, "Energy",         8.8,  5600),
    "ENLT":  ("Enlight Energy",            16.80, "Energy",        35.2,  7300),
    "FIBI":  ("First International Bank",  72.50, "Financials",     7.9,  5100),
    "FORTY": ("Formula Systems",           92.00, "Technology",    16.4,  2800),
    "GZIT":  ("Gazit Globe",               34.20, "Real Estate",   18.9,  4100),
    "ISRA":  ("Isracard",                  14.30, "Financials",    11.8,  3600),
    "MZTF":  ("Mizrahi Tefahot",           79.40, "Financials",     8.5, 18700),
    "NTML":  ("Neto Malinda",              42.10, "Consumer",      17.3,  1500),
    "PLSN":  ("Plus500",                   94.50, "Financials",    12.6,  3100),
    "RSEL":  ("Rami Levi",                123.00, "Consumer",      18.4,  2700),
    "SANO":  ("Sano Bruno",                48.20, "Consumer",      21.3,  1900),
    "SPNC":  ("Sapiens International",     38.70, "Technology",    23.8,  3200),
    "ORBI":  ("Orbia",                     83.20, "Industrial",    14.8,  2200),
    "PHAS":  ("Pharmos",                    1.85, "Healthcare",     0.0,   180),
    "WLFD":  ("Wellfield",                  6.10, "Technology",     0.0,   420),
    "IGLD":  ("Internet Gold",             28.50, "Technology",    22.7,  1200),
    "KCAL":  ("Keshet Media",              38.90, "Media",         19.2,  1800),
}

# Yahoo Finance tickers for Israeli indices
_INDEX_TICKERS = {
    "TA-35":  "^TA35",
    "TA-90":  "^TA90",
    "TA-125": "^TA125",
}


# ── Mock helpers ──────────────────────────────────────────────────────────────

def _mock_quote(symbol: str) -> Quote:
    """Generate a deterministic mock quote for a symbol, seeded by date."""
    name, base_price, sector, pe, mktcap = _UNIVERSE[symbol]
    random.seed(hash(symbol + datetime.now().strftime("%Y%m%d")))
    change_pct = random.uniform(-2.5, 2.5)
    change = round(base_price * change_pct / 100, 2)
    price = round(base_price + change, 2)
    volume = random.randint(50_000, 2_000_000)
    avg_vol = int(volume * random.uniform(0.7, 1.3))
    return Quote(
        symbol=symbol,
        name=name,
        price=price,
        change=change,
        change_pct=round(change_pct, 2),
        volume=volume,
        avg_volume_20d=avg_vol,
        open=round(base_price * random.uniform(0.99, 1.01), 2),
        high=round(price * random.uniform(1.001, 1.015), 2),
        low=round(price * random.uniform(0.985, 0.999), 2),
        prev_close=round(base_price, 2),
        week52_high=round(base_price * random.uniform(1.05, 1.35), 2),
        week52_low=round(base_price * random.uniform(0.65, 0.95), 2),
        market_cap=mktcap,
        pe_ratio=pe,
        sector=sector,
        as_of=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )


def _mock_ohlcv(symbol: str, days: int = 60) -> list[OHLCV]:
    """Generate deterministic mock OHLCV history, skipping weekends."""
    _, base_price, _, _, _ = _UNIVERSE[symbol]
    random.seed(hash(symbol))
    records = []
    price = base_price * random.uniform(0.85, 1.0)
    for i in range(days, 0, -1):
        dt = datetime.now() - timedelta(days=i)
        if dt.weekday() >= 5:  # skip Sat/Sun (TASE closed)
            continue
        change = price * random.uniform(-0.025, 0.025)
        close = round(max(price + change, 0.01), 2)
        high = round(max(price, close) * random.uniform(1.001, 1.015), 2)
        low = round(min(price, close) * random.uniform(0.985, 0.999), 2)
        records.append(OHLCV(
            symbol=symbol,
            date=dt.strftime("%Y-%m-%d"),
            open=round(price, 2),
            high=high,
            low=low,
            close=close,
            volume=random.randint(100_000, 3_000_000),
        ))
        price = close
    return records


# ── Live helpers (Yahoo Finance) ──────────────────────────────────────────────

def _yf_symbol(symbol: str) -> str:
    """Append the Yahoo Finance TASE suffix to a bare ticker symbol."""
    return f"{symbol}{_YF_SUFFIX}"


def _fetch_live_quote(symbol: str) -> Quote:
    """Fetch a live quote from Yahoo Finance for a TASE symbol."""
    import yfinance as yf
    ticker = yf.Ticker(_yf_symbol(symbol))
    info = ticker.info

    price = float(info.get("currentPrice") or info.get("regularMarketPrice") or 0)
    prev_close = float(info.get("previousClose") or info.get("regularMarketPreviousClose") or price)
    change = round(price - prev_close, 2)
    change_pct = round((change / prev_close) * 100, 2) if prev_close else 0.0

    # Fallbacks from our universe if Yahoo doesn't have the field
    name_fb, _, sector_fb, pe_fb, mktcap_fb = _UNIVERSE.get(symbol, (symbol, price, "Unknown", 0.0, 0))
    mktcap_raw = info.get("marketCap") or 0

    return Quote(
        symbol=symbol,
        name=info.get("longName") or info.get("shortName") or name_fb,
        price=price,
        change=change,
        change_pct=change_pct,
        volume=int(info.get("volume") or info.get("regularMarketVolume") or 0),
        avg_volume_20d=int(info.get("averageVolume") or info.get("averageVolume10days") or 0),
        open=float(info.get("open") or info.get("regularMarketOpen") or price),
        high=float(info.get("dayHigh") or info.get("regularMarketDayHigh") or price),
        low=float(info.get("dayLow") or info.get("regularMarketDayLow") or price),
        prev_close=prev_close,
        week52_high=float(info.get("fiftyTwoWeekHigh") or 0),
        week52_low=float(info.get("fiftyTwoWeekLow") or 0),
        market_cap=round(mktcap_raw / 1_000_000, 1) if mktcap_raw else mktcap_fb,
        pe_ratio=float(info.get("trailingPE") or info.get("forwardPE") or pe_fb),
        sector=info.get("sector") or sector_fb,
        as_of=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )


def _fetch_live_ohlcv(symbol: str, days: int = 60) -> list[OHLCV]:
    """Fetch OHLCV history from Yahoo Finance for a TASE symbol."""
    import yfinance as yf
    hist = yf.Ticker(_yf_symbol(symbol)).history(period=f"{days}d")
    return [
        OHLCV(
            symbol=symbol,
            date=date.strftime("%Y-%m-%d"),
            open=round(float(row["Open"]), 2),
            high=round(float(row["High"]), 2),
            low=round(float(row["Low"]), 2),
            close=round(float(row["Close"]), 2),
            volume=int(row["Volume"]),
        )
        for date, row in hist.iterrows()
    ]


def _fetch_live_index(name: str, ticker_sym: str) -> IndexSnapshot:
    """Fetch a live index snapshot from Yahoo Finance."""
    import yfinance as yf
    fi = yf.Ticker(ticker_sym).fast_info
    value = float(fi.last_price or 0)
    prev_close = float(fi.previous_close or value)
    change = round(value - prev_close, 2)
    change_pct = round((change / prev_close) * 100, 2) if prev_close else 0.0
    return IndexSnapshot(
        name=name,
        value=value,
        change=change,
        change_pct=change_pct,
        as_of=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )


# ── Main client ───────────────────────────────────────────────────────────────

class TASEClient:
    """TASE market data client with mock and live (Yahoo Finance) modes.

    TASE_MOCK=true  → deterministic mock data (fast, no network)
    TASE_MOCK=false → live data from Yahoo Finance (free, no API key needed)

    All public methods have identical signatures regardless of mode,
    so switching to live requires only changing the env var.
    """

    def __init__(self) -> None:
        """Initialise the client and log the active data mode."""
        self.mock = MOCK_MODE
        mode = "MOCK" if self.mock else "LIVE (Yahoo Finance)"
        logger.info("TASEClient initialized in %s mode", mode)

    def get_quote(self, symbol: str) -> Quote:
        """Get latest quote for a single TASE symbol."""
        if symbol not in _UNIVERSE:
            raise ValueError(
                f"Symbol '{symbol}' not in tracked universe. "
                f"Available: {sorted(_UNIVERSE.keys())}"
            )
        if self.mock:
            return _mock_quote(symbol)
        try:
            return _fetch_live_quote(symbol)
        except Exception as e:
            logger.warning("Live quote failed for %s: %s. Falling back to mock.", symbol, e)
            return _mock_quote(symbol)

    def get_quotes(self, symbols: list[str] | None = None) -> dict[str, Quote]:
        """Get quotes for multiple symbols (default: full universe).

        Args:
            symbols: List of ticker symbols, or None to fetch all universe tickers.

        Returns:
            Dict mapping symbol to Quote.
        """
        syms = symbols or list(_UNIVERSE.keys())
        return {s: self.get_quote(s) for s in syms}

    def get_ohlcv(self, symbol: str, days: int = 60) -> list[OHLCV]:
        """Get OHLCV history sorted oldest to newest.

        Args:
            symbol: TASE ticker symbol.
            days: Number of trading days to fetch. Defaults to 60.

        Returns:
            List of OHLCV bars, oldest first.
        """
        if symbol not in _UNIVERSE:
            raise ValueError(f"Symbol '{symbol}' not in tracked universe.")
        if self.mock:
            return _mock_ohlcv(symbol, days)
        try:
            return _fetch_live_ohlcv(symbol, days)
        except Exception as e:
            logger.warning("Live OHLCV failed for %s: %s. Falling back to mock.", symbol, e)
            return _mock_ohlcv(symbol, days)

    def get_index_snapshot(self) -> list[IndexSnapshot]:
        """Get snapshots for TA-35, TA-90, and TA-125."""
        if self.mock:
            random.seed(datetime.now().strftime("%Y%m%d"))
            result = []
            for name, val in {"TA-35": 2082.0, "TA-90": 1320.0, "TA-125": 1645.0}.items():
                chg = random.uniform(-8, 8)
                result.append(IndexSnapshot(
                    name=name,
                    value=round(val + chg, 2),
                    change=round(chg, 2),
                    change_pct=round(chg / val * 100, 2),
                    as_of=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                ))
            return result

        result = []
        for name, ticker_sym in _INDEX_TICKERS.items():
            try:
                result.append(_fetch_live_index(name, ticker_sym))
            except Exception as e:
                logger.warning("Index snapshot failed for %s: %s", name, e)
        return result

    def get_universe(self) -> list[str]:
        """Return sorted list of all tracked symbols."""
        return sorted(_UNIVERSE.keys())

    def get_universe_info(self) -> dict[str, dict]:
        """Return metadata dict for all symbols."""
        return {
            sym: {"name": d[0], "sector": d[2], "pe_ratio": d[3], "market_cap": d[4]}
            for sym, d in _UNIVERSE.items()
        }

    def is_market_open(self) -> bool:
        """Return True if TASE is currently in trading hours.

        TASE trades Sun–Thu 09:59–17:25 IST.
        """
        try:
            import pytz
            tz = pytz.timezone("Asia/Jerusalem")
        except ImportError:
            from datetime import timezone, timedelta as td
            tz = timezone(td(hours=3))  # IST fallback
        now = datetime.now(tz)
        if now.weekday() in (4, 5):  # Fri, Sat → closed
            return False
        open_t = now.replace(hour=9, minute=59, second=0, microsecond=0)
        close_t = now.replace(hour=17, minute=25, second=0, microsecond=0)
        return open_t <= now <= close_t

    def close(self) -> None:
        """No persistent connection — kept for API compatibility."""
        pass
