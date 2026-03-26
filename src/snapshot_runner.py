"""
Snapshot Runner
===============
Scores ALL recommendations at 7/30/90 day checkpoints automatically,
regardless of whether they were approved, rejected, or still pending.

Horizons: 7d (end of week), 30d (monthly), 90d (quarterly).
60d removed — redundant between 30d and 90d for a weekly trading cycle.

Called after every pipeline run. Safe to call when DB is unavailable —
all errors are caught and logged; the pipeline never crashes.
"""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

SNAPSHOT_DAYS = [7, 30, 90]


def fetch_ta35_current() -> float | None:
    """Fetch the current TA-35 index closing price from Yahoo Finance.

    Returns:
        Latest close as a float, or None if the fetch fails.
    """
    try:
        import yfinance as yf
        hist = yf.Ticker("TA35.TA").history(period="2d")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception as e:
        logger.warning("fetch_ta35_current failed: %s", e)
    return None


def fetch_price(ticker: str) -> float | None:
    """Fetch the current market price for a TASE or Nasdaq-listed ticker.

    Strategy:
        1. Try ``{ticker}.TA`` on Yahoo Finance (TASE listing).
        2. Fall back to bare ``{ticker}`` (Nasdaq/NYSE listing).

    Args:
        ticker: TASE symbol (e.g. "TEVA") or Nasdaq symbol (e.g. "CHKP").

    Returns:
        Latest close as a float, or None if both attempts fail.
    """
    try:
        import yfinance as yf
        hist = yf.Ticker(f"{ticker}.TA").history(period="2d")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
        # Nasdaq / NYSE fallback
        hist = yf.Ticker(ticker).history(period="2d")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception as e:
        logger.warning("fetch_price(%s) failed: %s", ticker, e)
    return None


def run_snapshots() -> dict:
    """Check all recommendations for overdue snapshots and score them.

    For each recommendation × horizon pair that is now due (age >= N days)
    but not yet recorded, fetches the current price and saves a snapshot row
    with return_pct, alpha_pct, and was_correct computed from today's price.

    Returns:
        Summary dict with keys ``processed`` (int), ``skipped`` (int),
        ``errors`` (int).
    """
    from src.db.recommendations_db import (
        get_recs_needing_snapshots,
        init_snapshots_table,
        save_snapshot,
    )

    init_snapshots_table()
    recs = get_recs_needing_snapshots()
    if not recs:
        return {"processed": 0, "skipped": 0, "errors": 0}

    ta35_now = fetch_ta35_current()
    processed = skipped = errors = 0
    now = datetime.now(timezone.utc)

    for rec in recs:
        # created_at may be tz-aware or naive depending on psycopg2 / pg config
        created_at = rec["created_at"]
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)

        days_since = (now - created_at).days
        done = set(rec.get("done_snapshots") or [])

        for days in SNAPSHOT_DAYS:
            if days_since < days:
                continue
            if days in done:
                continue

            ticker = rec["symbol"]
            price_now = fetch_price(ticker)
            if price_now is None:
                logger.warning("run_snapshots: no price for %s, skipping %dd snapshot", ticker, days)
                skipped += 1
                continue

            try:
                save_snapshot(
                    rec_id=rec["id"],
                    snapshot_days=days,
                    price_now=price_now,
                    ta35_now=ta35_now,
                    ta35_at_rec=float(rec["ta35_at_entry"]) if rec.get("ta35_at_entry") else None,
                    price_at_rec=float(rec["price_entry"]),
                    action=rec["action"],
                )
                processed += 1
            except Exception as e:
                logger.warning(
                    "run_snapshots: save_snapshot failed for rec %s @ %dd: %s",
                    rec["id"], days, e,
                )
                errors += 1

    return {"processed": processed, "skipped": skipped, "errors": errors}
