"""Recommendation scorer — measures executed trades against the hurdle rate.

For each approved recommendation with a recorded execution price (price_actual),
this module fetches the current price via yfinance and computes an annualised
return relative to the mandate's hurdle_rate_pct.

  benchmark_return_7d  = rec_annualised_return(7d)  − hurdle_rate_pct
  benchmark_return_30d = rec_annualised_return(30d) − hurdle_rate_pct

Positive = beat the hurdle.  Negative = missed.  NULL = too early or unscoreable.

Run automatically on dashboard startup, or manually:
    py -3.12 -X utf8 src/recommendation_scorer.py
"""

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
import yfinance as yf

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DB_PATH = _PROJECT_ROOT / "data" / "portfolio.db"
_YAML_PATH = _PROJECT_ROOT / "portfolio.yaml"

# Numeric-only tickers are Israeli fund/ETF security IDs — yfinance cannot
# price these (no exchange listing), so we skip them cleanly.
_FUND_ID_CHARS = frozenset("0123456789")


def _is_fund_id(symbol: str) -> bool:
    """Return True if symbol looks like a numeric Israeli fund security ID.

    Args:
        symbol: The ticker/symbol string from the recommendation record.

    Returns:
        True for all-digit symbols (e.g. '5142088'), False for exchange tickers.
    """
    return bool(symbol) and all(c in _FUND_ID_CHARS for c in symbol)


def _load_hurdle_rate() -> float:
    """Read hurdle_rate_pct from portfolio.yaml.

    Returns:
        The hurdle rate as a percentage (e.g. 10.0 for 10%). Falls back to
        10.0 if the key is missing or the file cannot be parsed.
    """
    try:
        with open(_YAML_PATH, encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh)
        return float(cfg.get("mandate", {}).get("hurdle_rate_pct", 10.0))
    except Exception as exc:
        logger.warning("Could not read hurdle_rate_pct from portfolio.yaml: %s — using 10.0", exc)
        return 10.0


def _fetch_current_price(symbol: str) -> float | None:
    """Fetch the latest closing price for a TASE ticker via yfinance.

    Appends '.TA' if the symbol does not already end with it.

    Args:
        symbol: Ticker symbol (e.g. 'ESLT' or 'ESLT.TA').

    Returns:
        The latest closing price as a float, or None on any failure.
    """
    ticker = symbol if symbol.upper().endswith(".TA") else f"{symbol}.TA"
    try:
        hist = yf.Ticker(ticker).history(period="5d")
        if hist.empty:
            logger.warning("Scorer: yfinance returned empty history for %s", ticker)
            return None
        return float(hist["Close"].iloc[-1])
    except Exception as exc:
        logger.warning("Scorer: yfinance fetch failed for %s: %s", ticker, exc)
        return None


def _annualised_return(
    price_entry: float,
    price_now: float,
    holding_days: int,
) -> float:
    """Calculate the annualised percentage return over a fixed window.

    Args:
        price_entry: Entry price (price_actual from DB).
        price_now: Current market price.
        holding_days: Number of calendar days in the measurement window (7 or 30).

    Returns:
        Annualised return as a percentage (e.g. 12.5 for 12.5% p.a.).
    """
    raw_return = (price_now - price_entry) / price_entry
    return raw_return * (365.0 / holding_days) * 100.0


def score_recommendations() -> dict[str, int]:
    """Score all eligible executed recommendations against the hurdle rate.

    Reads hurdle_rate_pct from portfolio.yaml.
    Finds approved recs with price_actual set but missing 7d or 30d scores.
    Fetches current prices via yfinance (TASE tickers only — fund IDs are skipped).
    Computes annualised return vs hurdle and writes back to DB.

    Returns:
        Dict with keys: scored_7d (int), scored_30d (int), skipped (int), errors (int).
    """
    hurdle = _load_hurdle_rate()
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    results: dict[str, int] = {"scored_7d": 0, "scored_30d": 0, "skipped": 0, "errors": 0}

    try:
        conn = sqlite3.connect(str(_DB_PATH))
        conn.row_factory = sqlite3.Row
    except Exception as exc:
        logger.error("Scorer: cannot open DB at %s: %s", _DB_PATH, exc)
        return results

    print(f"[Scorer] DB: {_DB_PATH}")
    print(f"[Scorer] Hurdle rate: {hurdle:.1f}% p.a.")

    # Ensure scorer columns exist (idempotent — safe on every run)
    try:
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(recommendations)")
        existing_cols = {row[1] for row in cur.fetchall()}
        for col in ("benchmark_return_7d", "benchmark_return_30d"):
            if col not in existing_cols:
                conn.execute(f"ALTER TABLE recommendations ADD COLUMN {col} REAL")
        conn.commit()
    except Exception as exc:
        logger.warning("Scorer: column migration failed: %s", exc)

    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, symbol, action, price_actual, executed_at,
                   benchmark_return_7d, benchmark_return_30d
            FROM recommendations
            WHERE approved = 1
              AND price_actual IS NOT NULL
              AND (benchmark_return_7d IS NULL OR benchmark_return_30d IS NULL)
        """)
        recs: list[dict[str, Any]] = [dict(r) for r in cur.fetchall()]
    except Exception as exc:
        logger.error("Scorer: query failed: %s", exc)
        conn.close()
        return results

    print(f"[Scorer] Candidates: {len(recs)} rec(s) with missing scores")

    for rec in recs:
        rec_id = rec["id"]
        symbol = rec["symbol"] or ""
        price_actual = rec["price_actual"]

        # Fund IDs (all-numeric) cannot be priced via yfinance
        if _is_fund_id(symbol):
            print(f"  rec#{rec_id} {symbol}: SKIP — fund security ID (no yfinance price)")
            results["skipped"] += 1
            continue

        # Parse executed_at — stored as a plain string in SQLite
        executed_at_raw = rec.get("executed_at")
        if not executed_at_raw:
            print(f"  rec#{rec_id} {symbol}: SKIP — executed_at is NULL")
            results["skipped"] += 1
            continue

        try:
            executed_at = datetime.strptime(str(executed_at_raw), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            # Try date-only format as fallback
            try:
                executed_at = datetime.strptime(str(executed_at_raw), "%Y-%m-%d")
            except ValueError:
                print(f"  rec#{rec_id} {symbol}: SKIP — cannot parse executed_at '{executed_at_raw}'")
                results["skipped"] += 1
                continue

        days_elapsed = (now - executed_at).days
        need_7d  = rec["benchmark_return_7d"]  is None and days_elapsed >= 7
        need_30d = rec["benchmark_return_30d"] is None and days_elapsed >= 30

        if not need_7d and not need_30d:
            print(f"  rec#{rec_id} {symbol}: SKIP — only {days_elapsed}d elapsed, not yet due")
            results["skipped"] += 1
            continue

        # Fetch price once per rec (covers both windows)
        current_price = _fetch_current_price(symbol)
        if current_price is None:
            print(f"  rec#{rec_id} {symbol}: ERROR — yfinance returned no price")
            results["errors"] += 1
            continue

        updates: dict[str, float] = {}

        if need_7d:
            ann = _annualised_return(price_actual, current_price, 7)
            updates["benchmark_return_7d"] = round(ann - hurdle, 4)
            results["scored_7d"] += 1
            sign = "+" if updates["benchmark_return_7d"] >= 0 else ""
            print(
                f"  rec#{rec_id} {symbol}: 7d score = {sign}{updates['benchmark_return_7d']:.2f}%"
                f" (ann {ann:.1f}% vs hurdle {hurdle:.1f}%)"
            )

        if need_30d:
            ann = _annualised_return(price_actual, current_price, 30)
            updates["benchmark_return_30d"] = round(ann - hurdle, 4)
            results["scored_30d"] += 1
            sign = "+" if updates["benchmark_return_30d"] >= 0 else ""
            print(
                f"  rec#{rec_id} {symbol}: 30d score = {sign}{updates['benchmark_return_30d']:.2f}%"
                f" (ann {ann:.1f}% vs hurdle {hurdle:.1f}%)"
            )

        # Write scores back to DB
        try:
            set_clauses = ", ".join(f"{col} = :{col}" for col in updates)
            updates["rec_id"] = rec_id
            conn.execute(
                f"UPDATE recommendations SET {set_clauses} WHERE id = :rec_id",
                updates,
            )
            conn.commit()
        except Exception as exc:
            logger.warning("Scorer: DB write failed for rec#%s: %s", rec_id, exc)
            results["errors"] += 1

    conn.close()
    print(
        f"[Scorer] Done — 7d: {results['scored_7d']}, 30d: {results['scored_30d']}, "
        f"skipped: {results['skipped']}, errors: {results['errors']}"
    )
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    score_recommendations()
