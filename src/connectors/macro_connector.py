"""Fetch live macro data: BOI rate, CPI, USD/ILS, TA-35, TA-125."""

import datetime
import logging
import re
from decimal import Decimal
from typing import Optional

import requests
import yfinance as yf

from src.models.market import MacroSnapshot

logger = logging.getLogger(__name__)

# Fallback constants used when the BOI API is unreachable.
_BOI_RATE_FALLBACK = Decimal("4.50")
_CPI_FALLBACK = Decimal("3.2")

# BOI SDMX v2 endpoints (XML format, no API key needed).
# BR dataflow, MNT_RIB_BOI_D series = BOI base interest rate (daily).
_BOI_RATE_URL = (
    "https://edge.boi.gov.il/FusionEdgeServer/sdmx/v2/data/dataflow/"
    "BOI.STATISTICS/BR/1.0/MNT_RIB_BOI_D?startperiod=2025-01"
)
# PRI dataflow, CP000000_PCHYTY series = all-items CPI year-on-year pct change.
_BOI_CPI_URL = (
    "https://edge.boi.gov.il/FusionEdgeServer/sdmx/v2/data/dataflow/"
    "BOI.STATISTICS/PRI/1.0/CP000000_PCHYTY?startperiod=2025-01"
)


def fetch_boi_interest_rate() -> Optional[Decimal]:
    """Fetch the current BOI base interest rate from the Bank of Israel SDMX API.

    Uses the BR dataflow, series MNT_RIB_BOI_D (BOI base rate, daily).
    The endpoint returns SDMX-ML XML; OBS_VALUE attributes are extracted with
    regex. No API key required.

    Returns:
        Current BOI rate as Decimal, or None if unreachable or unparseable.
    """
    try:
        resp = requests.get(_BOI_RATE_URL, timeout=10)
        resp.raise_for_status()
        values = re.findall(r'OBS_VALUE="([^"]+)"', resp.text)
        if not values:
            raise ValueError("no OBS_VALUE found in BOI rate response")
        rate = float(values[-1])
        logger.info("macro_connector: BOI rate = %.2f%%", rate)
        return Decimal(str(rate))
    except Exception as exc:
        logger.warning("macro_connector: BOI rate fetch failed: %s", exc)
        return None


def fetch_boi_cpi() -> Optional[Decimal]:
    """Fetch the latest CPI year-on-year change from the Bank of Israel SDMX API.

    Uses the PRI dataflow, series CP000000_PCHYTY (all-items CPI, year-on-year
    percent change). No API key required.

    Returns:
        CPI year-on-year percent change as Decimal (rounded to 2dp),
        or None if unreachable or unparseable.
    """
    try:
        resp = requests.get(_BOI_CPI_URL, timeout=15)
        resp.raise_for_status()
        values = re.findall(r'OBS_VALUE="([^"]+)"', resp.text)
        if not values:
            raise ValueError("no OBS_VALUE found in BOI CPI response")
        cpi = float(values[-1])
        logger.info("macro_connector: CPI = %.2f%%", cpi)
        return Decimal(str(round(cpi, 2)))
    except Exception as exc:
        logger.warning("macro_connector: BOI CPI fetch failed: %s", exc)
        return None


def fetch_live_macro() -> MacroSnapshot:
    """Fetch a live macro snapshot combining BOI API and Yahoo Finance data.

    BOI interest rate and CPI are fetched from the Bank of Israel public SDMX
    API, with fallback to hardcoded constants if the API is unreachable.
    USD/ILS and index levels are fetched from Yahoo Finance.

    Returns:
        A MacroSnapshot populated with the latest available data.
        Any field that fails to fetch is left as None.
    """
    today = datetime.date.today()

    boi_rate = fetch_boi_interest_rate() or _BOI_RATE_FALLBACK
    cpi = fetch_boi_cpi() or _CPI_FALLBACK

    usd_ils = _fetch_close("ILS=X", "USD/ILS")
    ta35 = _fetch_close("TA35.TA", "TA-35")
    ta125 = _fetch_close("^TA125.TA", "TA-125")

    return MacroSnapshot(
        date=today,
        boi_interest_rate=boi_rate,
        cpi_annual_pct=cpi,
        usd_ils_rate=Decimal(str(usd_ils)) if usd_ils is not None else None,
        ta35_close=Decimal(str(ta35)) if ta35 is not None else None,
        ta125_close=Decimal(str(ta125)) if ta125 is not None else None,
    )


def fetch_usdils_momentum() -> dict:
    """Fetch USD/ILS 30-day momentum trend via yfinance.

    Returns:
        Dict with current rate, 30d change pct, 7d change pct, trend direction,
        and interpretation for Israeli exporters vs domestic companies.
        Returns an empty dict if the fetch fails.
    """
    try:
        hist = yf.Ticker("USDILS=X").history(period="35d")
        if hist.empty or len(hist) < 5:
            logger.warning("macro_connector: USDILS=X returned insufficient data")
            return {}
        current    = float(hist["Close"].iloc[-1])
        month_ago  = float(hist["Close"].iloc[0])
        week_ago   = float(hist["Close"].iloc[-5])
        change_30d = round((current - month_ago) / month_ago * 100, 2)
        change_7d  = round((current - week_ago)  / week_ago  * 100, 2)

        if change_30d > 2:
            trend = "WEAKENING_SHEKEL"
            implication = (
                "Shekel weakening — tailwind for exporters (NICE, ESLT, TEVA, CHKP), "
                "headwind for importers"
            )
        elif change_30d < -2:
            trend = "STRENGTHENING_SHEKEL"
            implication = (
                "Shekel strengthening — headwind for exporters, "
                "tailwind for domestic companies"
            )
        else:
            trend = "STABLE"
            implication = "USD/ILS stable — neutral FX impact on exporters"

        logger.info(
            "macro_connector: USD/ILS current=%.4f 30d=%+.2f%% trend=%s",
            current, change_30d, trend,
        )
        return {
            "current":        round(current, 4),
            "change_30d_pct": change_30d,
            "change_7d_pct":  change_7d,
            "trend":          trend,
            "implication":    implication,
        }
    except Exception as exc:
        logger.warning("macro_connector: USD/ILS momentum fetch failed: %s", exc)
        return {}


def fetch_dividend_calendar(tickers: list[str]) -> list[dict]:
    """Fetch upcoming ex-dividend dates for TA-125 stocks via yfinance.

    Checks ex-dividend dates for the given tickers and returns those with
    ex-div dates in the next 30 days. Rate-limited to 50ms per request.

    Args:
        tickers: List of TASE ticker symbols (without .TA suffix).

    Returns:
        List of dicts sorted by ex_date, each with keys: ticker, ex_date,
        amount. Returns an empty list if no upcoming dividends are found.
    """
    import time
    from datetime import datetime, timedelta

    upcoming: list[dict] = []
    cutoff = datetime.now() + timedelta(days=30)

    for ticker in tickers[:50]:  # limit to avoid rate-limiting
        try:
            cal = yf.Ticker(f"{ticker}.TA").calendar
            if cal is None:
                continue
            # yfinance returns calendar as a dict
            ex_date = cal.get("Ex-Dividend Date")
            if not ex_date:
                continue
            # ex_date may be a date or datetime object
            if hasattr(ex_date, "date"):
                ex_date = ex_date.date()
            if datetime.now().date() <= ex_date <= cutoff.date():
                upcoming.append({
                    "ticker":  ticker,
                    "ex_date": str(ex_date),
                    "amount":  cal.get("Dividend Rate", "N/A"),
                })
            time.sleep(0.05)
        except Exception:
            continue

    result = sorted(upcoming, key=lambda x: x["ex_date"])
    logger.info("macro_connector: dividend calendar — %d events in next 30d", len(result))
    return result


def _fetch_close(ticker: str, label: str) -> float | None:
    """Fetch the most recent closing price for a Yahoo Finance ticker.

    Args:
        ticker: Yahoo Finance ticker symbol.
        label: Human-readable label used in log messages.

    Returns:
        Latest closing price as a float, or None if the fetch fails.
    """
    try:
        hist = yf.Ticker(ticker).history(period="2d")
        if hist.empty:
            logger.warning("macro_connector: no data for %s (%s)", label, ticker)
            return None
        return round(float(hist["Close"].iloc[-1]), 4)
    except Exception as exc:
        logger.warning("macro_connector: failed to fetch %s (%s): %s", label, ticker, exc)
        return None
