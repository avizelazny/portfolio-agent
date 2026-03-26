"""Fetch live/last-close prices for TASE and Nasdaq-listed tickers via yfinance.

Used to inject current market prices into Claude's context before generating
recommendations, giving the model intraday awareness beyond the portfolio snapshot.
"""

import logging
import time
from datetime import datetime
from decimal import Decimal

import yfinance as yf

from src.models.market import Holding

logger = logging.getLogger(__name__)

# Tickers known to trade on Nasdaq — tried as bare symbol if .TA fails.
_NASDAQ_SYMBOLS: set[str] = {
    "TEVA", "NICE", "CHKP", "AMDOCS", "MNDO", "GILT", "ESLT",
    "NVMI", "CAMT", "SPNS", "KMDA", "MGIC", "MCOM",
}


def fetch_live_prices(tickers: list[str]) -> dict[str, float]:
    """Fetch the most recent closing price for each ticker via yfinance.

    Tries ``{ticker}.TA`` for TASE listings first. Falls back to the bare
    ticker symbol for known Nasdaq dual-listed stocks. Tickers with no data
    are silently skipped.

    A 0.1-second delay is inserted between each fetch to avoid Yahoo Finance
    rate limiting.

    Args:
        tickers: List of ticker symbols (TASE format, e.g. "ESLT", "LUMI").

    Returns:
        Dict mapping ticker symbol to latest closing price as float.
        Only tickers with successfully fetched prices are included.
    """
    prices: dict[str, float] = {}

    for ticker in tickers:
        price = _fetch_close_tase(ticker)
        if price is None and ticker in _NASDAQ_SYMBOLS:
            price = _fetch_close_nasdaq(ticker)
        if price is not None:
            prices[ticker] = price
        time.sleep(0.1)

    logger.info("live_prices: fetched %d/%d tickers", len(prices), len(tickers))
    return prices


def _fetch_close_tase(ticker: str) -> float | None:
    """Fetch last close from Yahoo Finance using the .TA suffix.

    Args:
        ticker: TASE ticker symbol without suffix.

    Returns:
        Last closing price as float, or None on failure.
    """
    symbol = f"{ticker}.TA"
    try:
        hist = yf.Ticker(symbol).history(period="2d")
        if hist.empty:
            return None
        return round(float(hist["Close"].iloc[-1]), 4)
    except Exception as exc:
        logger.debug("live_prices: .TA fetch failed for %s: %s", ticker, exc)
        return None


def _fetch_close_nasdaq(ticker: str) -> float | None:
    """Fetch last close from Yahoo Finance using bare Nasdaq ticker.

    Args:
        ticker: Nasdaq ticker symbol.

    Returns:
        Last closing price as float, or None on failure.
    """
    try:
        hist = yf.Ticker(ticker).history(period="2d")
        if hist.empty:
            return None
        return round(float(hist["Close"].iloc[-1]), 4)
    except Exception as exc:
        logger.debug("live_prices: Nasdaq fetch failed for %s: %s", ticker, exc)
        return None


def format_live_prices_for_prompt(
    prices: dict[str, float],
    portfolio_holdings: list[Holding],
) -> str:
    """Format live prices as a concise text block for Claude's prompt context.

    Splits prices into two sections: portfolio holdings (always shown) and
    top movers from the broader universe (top 5 gainers + top 5 losers by
    single-day change where available).

    Args:
        prices: Dict of {ticker: price} as returned by fetch_live_prices().
        portfolio_holdings: List of Holding objects from the current portfolio,
            used to identify which tickers are held positions.

    Returns:
        Formatted multi-line string for inclusion as a <live_prices> XML block.
        Returns empty string if prices dict is empty.
    """
    if not prices:
        return ""

    now = datetime.now().strftime("%H:%M IST")
    held_tickers = {h.ticker for h in portfolio_holdings}

    # ── Holdings section ──────────────────────────────────────────────────────
    holding_lines: list[str] = []
    for h in portfolio_holdings:
        if h.ticker in prices:
            price = prices[h.ticker]
            holding_lines.append(f"{h.ticker}: \u20aa{price:,.2f}")

    # ── Universe movers (need prev close for % change — use last 2 bars) ─────
    mover_parts: list[tuple[float, str]] = []
    for ticker, price in prices.items():
        if ticker in held_tickers:
            continue
        # Fetch prev close inline for pct change — already cached in yf internally
        try:
            hist = yf.Ticker(f"{ticker}.TA").history(period="5d")
            if len(hist) >= 2:
                prev = float(hist["Close"].iloc[-2])
                if prev > 0:
                    pct = (price - prev) / prev * 100
                    mover_parts.append((pct, ticker))
        except Exception:
            pass

    mover_parts.sort(key=lambda x: x[0], reverse=True)
    gainers = mover_parts[:5]
    losers = mover_parts[-5:][::-1]

    lines = [f"LIVE MARKET PRICES (as of {now}):"]
    lines.append("")

    if holding_lines:
        lines.append("YOUR HOLDINGS — current prices:")
        lines.append("  " + " | ".join(holding_lines))

    if gainers or losers:
        lines.append("")
        lines.append("TA-125 UNIVERSE — top movers today:")
        if gainers:
            g_str = " | ".join(
                f"{t}: \u20aa{prices[t]:,.2f} (+{pct:.1f}%)" for pct, t in gainers
            )
            lines.append(f"  {g_str}")
        if losers:
            l_str = " | ".join(
                f"{t}: \u20aa{prices[t]:,.2f} ({pct:.1f}%)" for pct, t in losers
            )
            lines.append(f"  {l_str}")

    return "\n".join(lines)
