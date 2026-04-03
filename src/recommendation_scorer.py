"""Recommendation scorer — measures executed and rejected recs against the hurdle rate.

For each approved recommendation with a recorded execution price (price_actual),
this module fetches the current price via yfinance and computes an annualised
return relative to the mandate's hurdle_rate_pct.

  benchmark_return_7d  = rec_annualised_return(7d)  − hurdle_rate_pct
  benchmark_return_30d = rec_annualised_return(30d) − hurdle_rate_pct

For rejected recommendations (approved=0, closed=1) the same logic applies,
using created_at as the baseline date:

  unacted_return_7d  = rec_annualised_return(7d)  − hurdle_rate_pct
  unacted_return_30d = rec_annualised_return(30d) − hurdle_rate_pct

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

# All scorer columns that must exist before any query runs.
_SCORER_COLUMNS = [
    "benchmark_return_7d",
    "benchmark_return_30d",
    "unacted_return_7d",
    "unacted_return_30d",
]


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
        price_entry: Reference price (price_actual for acted, price_entry for unacted).
        price_now: Current market price.
        holding_days: Number of calendar days in the measurement window (7 or 30).

    Returns:
        Annualised return as a percentage (e.g. 12.5 for 12.5% p.a.).
    """
    raw_return = (price_now - price_entry) / price_entry
    return raw_return * (365.0 / holding_days) * 100.0


def _parse_date(raw: Any) -> datetime | None:
    """Parse a SQLite date string to a naive datetime.

    Tries '%Y-%m-%d %H:%M:%S' then '%Y-%m-%d' as a fallback.

    Args:
        raw: Value from a SQLite TEXT column (may be str or None).

    Returns:
        A naive datetime, or None if parsing fails.
    """
    if not raw:
        return None
    s = str(raw)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _ensure_columns(conn: sqlite3.Connection) -> None:
    """Add scorer columns to recommendations table if they are missing.

    Idempotent — safe to call on every run.

    Args:
        conn: Open SQLite connection.
    """
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(recommendations)")
    existing = {row[1] for row in cur.fetchall()}
    for col in _SCORER_COLUMNS:
        if col not in existing:
            conn.execute(f"ALTER TABLE recommendations ADD COLUMN {col} REAL")
    conn.commit()


def _score_pass(
    conn: sqlite3.Connection,
    recs: list[dict[str, Any]],
    col_7d: str,
    col_30d: str,
    date_field: str,
    price_field: str,
    hurdle: float,
    now: datetime,
    label: str,
) -> dict[str, int]:
    """Score one batch of recommendations (acted or unacted) against the hurdle.

    Generic inner loop used by both the acted and unacted passes. Fetches
    the current yfinance price once per rec and writes 7d/30d scores.

    Args:
        conn: Open SQLite connection (write access).
        recs: List of row dicts from the recommendations table.
        col_7d: DB column name for the 7-day score.
        col_30d: DB column name for the 30-day score.
        date_field: Name of the date field to use as the measurement baseline.
        price_field: Name of the entry price field.
        hurdle: Hurdle rate in percent (e.g. 10.0).
        now: Current naive UTC datetime.
        label: Short label for log lines ('acted' or 'unacted').

    Returns:
        Dict with keys: scored_7d, scored_30d, skipped, errors.
    """
    counts: dict[str, int] = {"scored_7d": 0, "scored_30d": 0, "skipped": 0, "errors": 0}

    for rec in recs:
        rec_id = rec["id"]
        symbol = rec["symbol"] or ""
        price_ref = rec.get(price_field)

        if _is_fund_id(symbol):
            print(f"  [{label}] rec#{rec_id} {symbol}: SKIP — fund security ID")
            counts["skipped"] += 1
            continue

        if not price_ref:
            print(f"  [{label}] rec#{rec_id} {symbol}: SKIP — {price_field} is NULL")
            counts["skipped"] += 1
            continue

        baseline = _parse_date(rec.get(date_field))
        if not baseline:
            print(f"  [{label}] rec#{rec_id} {symbol}: SKIP — cannot parse {date_field} '{rec.get(date_field)}'")
            counts["skipped"] += 1
            continue

        days_elapsed = (now - baseline).days
        need_7d  = rec[col_7d]  is None and days_elapsed >= 7
        need_30d = rec[col_30d] is None and days_elapsed >= 30

        if not need_7d and not need_30d:
            print(f"  [{label}] rec#{rec_id} {symbol}: SKIP — {days_elapsed}d elapsed, not yet due")
            counts["skipped"] += 1
            continue

        current_price = _fetch_current_price(symbol)
        if current_price is None:
            print(f"  [{label}] rec#{rec_id} {symbol}: ERROR — yfinance returned no price")
            counts["errors"] += 1
            continue

        updates: dict[str, Any] = {}

        if need_7d:
            ann = _annualised_return(float(price_ref), current_price, 7)
            updates[col_7d] = round(ann - hurdle, 4)
            counts["scored_7d"] += 1
            sign = "+" if updates[col_7d] >= 0 else ""
            print(
                f"  [{label}] rec#{rec_id} {symbol}: 7d = {sign}{updates[col_7d]:.2f}%"
                f" (ann {ann:.1f}% vs hurdle {hurdle:.1f}%)"
            )

        if need_30d:
            ann = _annualised_return(float(price_ref), current_price, 30)
            updates[col_30d] = round(ann - hurdle, 4)
            counts["scored_30d"] += 1
            sign = "+" if updates[col_30d] >= 0 else ""
            print(
                f"  [{label}] rec#{rec_id} {symbol}: 30d = {sign}{updates[col_30d]:.2f}%"
                f" (ann {ann:.1f}% vs hurdle {hurdle:.1f}%)"
            )

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
            counts["errors"] += 1

    return counts


def score_recommendations() -> dict[str, int]:
    """Score all eligible recommendations (acted and unacted) against the hurdle rate.

    Pass 1 — acted: approved=1, price_actual set, missing benchmark_return_7d/30d.
    Pass 2 — unacted: approved=0, closed=1, price_entry set, missing unacted_return_7d/30d.

    Both passes use the same annualised return formula and the same hurdle_rate_pct
    from portfolio.yaml. Fund security IDs (all-numeric) are skipped in both passes.

    Returns:
        Dict with keys:
            acted_7d, acted_30d — recs scored in the acted pass.
            unacted_7d, unacted_30d — recs scored in the unacted pass.
            skipped, errors — totals across both passes.
    """
    hurdle = _load_hurdle_rate()
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    totals: dict[str, int] = {
        "acted_7d": 0, "acted_30d": 0,
        "unacted_7d": 0, "unacted_30d": 0,
        "skipped": 0, "errors": 0,
    }

    try:
        conn = sqlite3.connect(str(_DB_PATH))
        conn.row_factory = sqlite3.Row
    except Exception as exc:
        logger.error("Scorer: cannot open DB at %s: %s", _DB_PATH, exc)
        return totals

    print(f"[Scorer] DB: {_DB_PATH}")
    print(f"[Scorer] Hurdle rate: {hurdle:.1f}% p.a.")

    _ensure_columns(conn)

    # ── Pass 1: acted (approved, executed) ────────────────────────────────────
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
        acted_recs: list[dict[str, Any]] = [dict(r) for r in cur.fetchall()]
    except Exception as exc:
        logger.error("Scorer: acted query failed: %s", exc)
        conn.close()
        return totals

    print(f"[Scorer] Acted candidates: {len(acted_recs)}")
    c = _score_pass(
        conn, acted_recs,
        col_7d="benchmark_return_7d", col_30d="benchmark_return_30d",
        date_field="executed_at", price_field="price_actual",
        hurdle=hurdle, now=now, label="acted",
    )
    totals["acted_7d"]  += c["scored_7d"]
    totals["acted_30d"] += c["scored_30d"]
    totals["skipped"]   += c["skipped"]
    totals["errors"]    += c["errors"]

    # ── Pass 2: unacted (rejected/closed, no execution) ───────────────────────
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, symbol, action, price_entry, created_at,
                   unacted_return_7d, unacted_return_30d
            FROM recommendations
            WHERE approved = 0
              AND closed = 1
              AND price_entry IS NOT NULL
              AND (unacted_return_7d IS NULL OR unacted_return_30d IS NULL)
        """)
        unacted_recs: list[dict[str, Any]] = [dict(r) for r in cur.fetchall()]
    except Exception as exc:
        logger.error("Scorer: unacted query failed: %s", exc)
        conn.close()
        return totals

    print(f"[Scorer] Unacted candidates: {len(unacted_recs)}")
    c = _score_pass(
        conn, unacted_recs,
        col_7d="unacted_return_7d", col_30d="unacted_return_30d",
        date_field="created_at", price_field="price_entry",
        hurdle=hurdle, now=now, label="unacted",
    )
    totals["unacted_7d"]  += c["scored_7d"]
    totals["unacted_30d"] += c["scored_30d"]
    totals["skipped"]     += c["skipped"]
    totals["errors"]      += c["errors"]

    conn.close()
    print(
        f"[Scorer] Done — "
        f"acted 7d: {totals['acted_7d']}, 30d: {totals['acted_30d']} | "
        f"unacted 7d: {totals['unacted_7d']}, 30d: {totals['unacted_30d']} | "
        f"skipped: {totals['skipped']}, errors: {totals['errors']}"
    )
    return totals


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    score_recommendations()
