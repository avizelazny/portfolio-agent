"""Fetches live prices for continuously-traded TASE securities via yfinance.

Yahoo Finance uses the .TA suffix for TASE-listed securities.
Example: security_id "5142088" → ticker "5142088.TA"

Some ETFs have alphanumeric tickers on Yahoo Finance that do not match their
numeric TASE security ID.  The ``_SYMBOL_MAP`` below maps known TASE IDs to
their correct Yahoo Finance symbols.

Known unmapped funds (no Yahoo Finance coverage as of 2026-03):
  1235985 — תכלית סל (4A) אינדקס תעשיות ביטחוניות ישראל
            Launched Nov 2025; not yet indexed by Yahoo Finance.
            Workaround: set pricing to a static value in portfolio.yaml.
"""
import logging
from typing import Optional

import yfinance as yf

logger = logging.getLogger(__name__)

# Maps numeric TASE security IDs to their Yahoo Finance ticker symbols.
# Needed when the ETF's Yahoo Finance symbol differs from "{id}.TA".
_SYMBOL_MAP: dict[str, str] = {
    "1148907": "HRL-F7.TA",  # הראל סל (4A) ת"א 35
}


def fetch_continuous_price(security_id: str) -> Optional[float]:
    """Fetch the latest price for a TASE-listed security using Yahoo Finance.

    Uses ``_SYMBOL_MAP`` to resolve known IDs to their Yahoo Finance tickers;
    falls back to ``{security_id}.TA`` for unlisted IDs.

    Tries ``ticker.fast_info['last_price']`` first (fastest, no HTTP roundtrip
    beyond the initial quote fetch). Falls back to ``ticker.history(period='1d')``
    if ``fast_info`` raises or returns a falsy value.

    Args:
        security_id: TASE security ID as a string (e.g. "1148907").

    Returns:
        Latest price in ILS as a float, or None if the fetch fails for any reason.
    """
    yahoo_sym = _SYMBOL_MAP.get(security_id, f"{security_id}.TA")
    try:
        ticker = yf.Ticker(yahoo_sym)

        # Strategy 1: fast_info — single cached fetch, no full history download
        try:
            price = ticker.fast_info["last_price"]
            if price:
                print(f"  ✓ {yahoo_sym}: ₪{float(price):.2f}")
                return float(price)
        except Exception:
            pass

        # Strategy 2: recent history — more reliable but slower
        hist = ticker.history(period="1d")
        if not hist.empty and "Close" in hist.columns:
            price = float(hist["Close"].iloc[-1])
            print(f"  ✓ {yahoo_sym}: ₪{price:.2f}")
            return price

        logger.warning("fetch_continuous_price: no data for %s", yahoo_sym)
        print(f"  ⚠ {yahoo_sym}: price unavailable")
        return None

    except Exception as e:
        logger.warning("fetch_continuous_price: error fetching %s: %s", yahoo_sym, e)
        print(f"  ⚠ {yahoo_sym}: price unavailable")
        return None
