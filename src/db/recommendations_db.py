"""Production-grade feedback storage for the portfolio agent.

Uses SQLite for local development (data/portfolio.db).
For AWS deployment, swap this module for the psycopg2/PostgreSQL version
— see requirements.txt for the commented-out psycopg2-binary dependency.
"""

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from src.models.recommendation import (
    ApprovalUpdate,
    OutcomeUpdate,
    PerformanceSummary,
    RecommendationRecord,
)

logger = logging.getLogger(__name__)

# Anchor to the project root (src/db/ → src/ → project root) so the path
# is correct regardless of the working directory approve.py is launched from.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DB_DIR = _PROJECT_ROOT / "data"
_DB_PATH = _DB_DIR / "portfolio.db"


# ── Connection ────────────────────────────────────────────────────────────────

def get_connection() -> sqlite3.Connection:
    """Open and return a SQLite connection with Row factory enabled.

    Creates the data/ directory if it does not exist.

    Returns:
        A sqlite3.Connection with row_factory set to sqlite3.Row.
    """
    _DB_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


# Internal alias kept for consistency with original module
_get_conn = get_connection


def _float(v: object) -> float | None:
    """Safely convert a Decimal or numeric value to float for SQLite storage."""
    if v is None:
        return None
    return float(v)


def _params(d: dict) -> dict:
    """Convert any Decimal values in a dict to float for SQLite compatibility."""
    return {k: _float(v) if isinstance(v, Decimal) else v for k, v in d.items()}


# ── Schema ────────────────────────────────────────────────────────────────────

def init_recommendations_table() -> None:
    """Create or migrate the recommendations table and its indexes.

    Safe to call on every startup — uses CREATE TABLE IF NOT EXISTS.
    """
    sql = """
    CREATE TABLE IF NOT EXISTS recommendations (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at       TEXT    DEFAULT (datetime('now')),

        symbol           TEXT    NOT NULL,
        action           TEXT    NOT NULL,
        conviction       TEXT    NOT NULL,
        thesis           TEXT    NOT NULL,
        key_risk         TEXT    NOT NULL,
        run_type         TEXT    DEFAULT 'morning',

        price_entry      REAL,
        price_target     REAL,
        ta35_at_entry    REAL,

        approved         INTEGER,
        actual_price     REAL,
        quantity         INTEGER,
        approval_note    TEXT,
        approved_at      TEXT,

        price_current    REAL,
        ta35_current     REAL,
        last_updated     TEXT,

        closed           INTEGER DEFAULT 0,
        price_exit       REAL,
        exit_date        TEXT,
        holding_days     INTEGER,

        return_pct       REAL,
        benchmark_pct    REAL,
        alpha            REAL,
        success          INTEGER,

        -- Quality-tracker columns (added via migration for existing DBs)
        current_price         REAL,
        current_return_pct    REAL,
        net_return_pct        REAL,
        direction_correct     INTEGER,
        transaction_cost_pct  REAL,
        last_scored_at        TEXT
    )
    """
    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_rec_symbol     ON recommendations(symbol)",
        "CREATE INDEX IF NOT EXISTS idx_rec_created_at ON recommendations(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_rec_approved   ON recommendations(approved)",
        "CREATE INDEX IF NOT EXISTS idx_rec_closed     ON recommendations(closed)",
        "CREATE INDEX IF NOT EXISTS idx_rec_action     ON recommendations(action)",
        "CREATE INDEX IF NOT EXISTS idx_rec_conviction ON recommendations(conviction)",
    ]
    try:
        conn = get_connection()
        with conn:
            cur = conn.cursor()
            cur.execute(sql)
            for idx in indexes:
                cur.execute(idx)
        conn.close()
        logger.info("recommendations table ready")
    except Exception as e:
        logger.warning("init_recommendations_table failed: %s", e)

    _migrate_quality_columns()
    _migrate_execution_columns()
    _migrate_scorer_columns()
    _migrate_price_limit_column()


def _migrate_execution_columns() -> None:
    """Add execution-tracking columns to an existing recommendations table.

    Safe to run on every startup — skips columns that already exist.
    These columns are populated automatically by trade_matcher when a
    Bank Discount ביצועים היסטוריים export is uploaded.
    """
    new_columns = [
        ("price_actual", "REAL"),
        ("qty_actual",   "REAL"),
        ("executed_at",  "TEXT"),
    ]
    try:
        conn = get_connection()
        with conn:
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(recommendations)")
            existing = {row["name"] for row in cur.fetchall()}
            for col_name, col_type in new_columns:
                if col_name not in existing:
                    cur.execute(
                        f"ALTER TABLE recommendations ADD COLUMN {col_name} {col_type}"
                    )
                    logger.info("Migration: added column %s to recommendations", col_name)
        conn.close()
    except Exception as exc:
        logger.warning("_migrate_execution_columns failed: %s", exc)


def _migrate_scorer_columns() -> None:
    """Add hurdle-rate scorer columns to an existing recommendations table.

    Safe to run on every startup — skips columns that already exist.
    These columns are populated by recommendation_scorer.score_recommendations().
    """
    new_columns = [
        ("benchmark_return_7d",  "REAL"),
        ("benchmark_return_30d", "REAL"),
        ("unacted_return_7d",    "REAL"),
        ("unacted_return_30d",   "REAL"),
    ]
    try:
        conn = get_connection()
        with conn:
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(recommendations)")
            existing = {row["name"] for row in cur.fetchall()}
            for col_name, col_type in new_columns:
                if col_name not in existing:
                    cur.execute(
                        f"ALTER TABLE recommendations ADD COLUMN {col_name} {col_type}"
                    )
                    logger.info("Migration: added column %s to recommendations", col_name)
        conn.close()
    except Exception as exc:
        logger.warning("_migrate_scorer_columns failed: %s", exc)


def _migrate_price_limit_column() -> None:
    """Add price_limit column to an existing recommendations table.

    Safe to run on every startup — skips if the column already exists.
    price_limit stores the limit order entry price set at approval time,
    preserving it even after the order expires and is removed from portfolio.yaml.
    """
    try:
        conn = get_connection()
        with conn:
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(recommendations)")
            existing = {row["name"] for row in cur.fetchall()}
            if "price_limit" not in existing:
                conn.execute("ALTER TABLE recommendations ADD COLUMN price_limit REAL")
                logger.info("Migration: added column price_limit to recommendations")
        conn.close()
    except Exception as exc:
        logger.warning("_migrate_price_limit_column failed: %s", exc)


def set_price_limit(rec_id: int, price_limit: float) -> None:
    """Store the limit order entry price on a recommendation record.

    Called at approval time when the user specifies a limit price.
    price_limit is preserved permanently on the rec even after the order
    is removed from portfolio.yaml pending_orders.

    Args:
        rec_id: DB id of the recommendation to update.
        price_limit: The limit order entry price in ILS.
    """
    try:
        conn = get_connection()
        with conn:
            conn.execute(
                "UPDATE recommendations SET price_limit = ? WHERE id = ?",
                (price_limit, rec_id),
            )
        conn.close()
        logger.info("set_price_limit: rec#%s limit=%.4f", rec_id, price_limit)
    except Exception as exc:
        logger.warning("set_price_limit failed for rec#%s: %s", rec_id, exc)


def _migrate_quality_columns() -> None:
    """Add quality-scoring columns to an existing recommendations table.

    Safe to run on every startup — skips columns that already exist.
    Required because ALTER TABLE cannot use IF NOT EXISTS in SQLite < 3.37.
    """
    new_columns = [
        ("current_price",        "REAL"),
        ("current_return_pct",   "REAL"),
        ("net_return_pct",       "REAL"),
        ("direction_correct",    "INTEGER"),
        ("transaction_cost_pct", "REAL"),
        ("last_scored_at",       "TEXT"),
    ]
    try:
        conn = get_connection()
        with conn:
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(recommendations)")
            existing = {row["name"] for row in cur.fetchall()}
            for col_name, col_type in new_columns:
                if col_name not in existing:
                    cur.execute(
                        f"ALTER TABLE recommendations ADD COLUMN {col_name} {col_type}"
                    )
                    logger.info("Migration: added column %s to recommendations", col_name)
        conn.close()
    except Exception as exc:
        logger.warning("_migrate_quality_columns failed: %s", exc)


def init_snapshots_table() -> None:
    """Create the recommendation_snapshots table and its indexes if they don't exist.

    Safe to call on every startup.
    """
    sql = """
    CREATE TABLE IF NOT EXISTS recommendation_snapshots (
        id                 INTEGER PRIMARY KEY AUTOINCREMENT,
        rec_id             INTEGER REFERENCES recommendations(id),
        snapshot_days      INTEGER CHECK (snapshot_days IN (7, 30, 90)),
        price_at_snapshot  REAL,
        ta35_at_snapshot   REAL,
        ta35_at_rec        REAL,
        return_pct         REAL,
        ta35_return_pct    REAL,
        alpha_pct          REAL,
        was_correct        INTEGER,
        snapshot_date      TEXT DEFAULT (datetime('now')),
        UNIQUE(rec_id, snapshot_days)
    )
    """
    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_snap_rec_id ON recommendation_snapshots(rec_id)",
        "CREATE INDEX IF NOT EXISTS idx_snap_days   ON recommendation_snapshots(snapshot_days)",
    ]
    try:
        conn = get_connection()
        with conn:
            cur = conn.cursor()
            cur.execute(sql)
            for idx in indexes:
                cur.execute(idx)
        conn.close()
        logger.info("recommendation_snapshots table ready")
    except Exception as e:
        logger.warning("init_snapshots_table failed: %s", e)


# ── Write operations ──────────────────────────────────────────────────────────

def save_recommendation(rec: RecommendationRecord) -> int | None:
    """Insert a new recommendation row and return its generated DB id.

    Args:
        rec: The recommendation to persist.

    Returns:
        The new row's integer id, or None if the insert failed.
    """
    sql = """
    INSERT INTO recommendations
        (symbol, action, conviction, thesis, key_risk,
         price_entry, price_target, ta35_at_entry, run_type)
    VALUES
        (:symbol, :action, :conviction, :thesis, :key_risk,
         :price_entry, :price_target, :ta35_at_entry, :run_type)
    """
    try:
        conn = get_connection()
        with conn:
            cur = conn.cursor()
            cur.execute(sql, _params(rec.model_dump()))
            new_id = cur.lastrowid
        conn.close()
        return new_id
    except Exception as e:
        logger.warning("save_recommendation failed: %s", e)
        return None


def update_approval(update: ApprovalUpdate) -> None:
    """Record the user's approval or rejection decision for a recommendation.

    Args:
        update: ApprovalUpdate containing rec_id, approved flag, and optional
            actual_price, quantity, and note fields.
    """
    sql = """
    UPDATE recommendations
    SET approved      = :approved,
        actual_price  = :actual_price,
        quantity      = :quantity,
        approval_note = :note,
        approved_at   = datetime('now')
    WHERE id = :rec_id
    """
    try:
        conn = get_connection()
        with conn:
            cur = conn.cursor()
            cur.execute(sql, _params(update.model_dump()))
        conn.close()
    except Exception as e:
        logger.warning("update_approval failed: %s", e)


def update_mark_to_market(
    rec_id: int,
    price_current: Decimal,
    ta35_current: Decimal | None = None,
) -> None:
    """Update the current price for an open position without closing it.

    Args:
        rec_id: DB id of the recommendation to update.
        price_current: Latest market price for the security.
        ta35_current: Current TA-35 index level for benchmark tracking.
    """
    sql = """
    UPDATE recommendations
    SET price_current = :price_current,
        ta35_current  = :ta35_current,
        last_updated  = datetime('now')
    WHERE id = :rec_id
      AND closed = 0
    """
    try:
        conn = get_connection()
        with conn:
            cur = conn.cursor()
            cur.execute(sql, {
                "rec_id": rec_id,
                "price_current": _float(price_current),
                "ta35_current": _float(ta35_current),
            })
        conn.close()
    except Exception as e:
        logger.warning("update_mark_to_market failed: %s", e)


def close_position(
    rec_id: int,
    price_exit: Decimal,
    ta35_at_exit: Decimal | None = None,
) -> None:
    """Close a position and calculate return, benchmark, alpha, and success flag.

    All performance metrics are computed in SQL at close time.

    Args:
        rec_id: DB id of the recommendation to close.
        price_exit: The price at which the position was exited.
        ta35_at_exit: TA-35 level at exit for benchmark comparison.
    """
    sql = """
    UPDATE recommendations
    SET closed        = 1,
        price_exit    = :price_exit,
        exit_date     = datetime('now'),
        holding_days  = CAST(julianday('now') - julianday(created_at) AS INTEGER),
        ta35_current  = :ta35_at_exit,

        return_pct    = CASE
                          WHEN price_entry IS NOT NULL AND price_entry > 0
                          THEN ROUND((:price_exit - price_entry) / price_entry * 100, 4)
                          ELSE NULL
                        END,

        benchmark_pct = CASE
                          WHEN ta35_at_entry IS NOT NULL AND ta35_at_entry > 0
                               AND :ta35_at_exit IS NOT NULL
                          THEN ROUND((:ta35_at_exit - ta35_at_entry) / ta35_at_entry * 100, 4)
                          ELSE NULL
                        END,

        alpha         = CASE
                          WHEN ta35_at_entry IS NOT NULL AND ta35_at_entry > 0
                               AND :ta35_at_exit IS NOT NULL
                               AND price_entry IS NOT NULL AND price_entry > 0
                          THEN ROUND(
                              ((:price_exit - price_entry) / price_entry * 100) -
                              ((:ta35_at_exit - ta35_at_entry) / ta35_at_entry * 100),
                              4)
                          ELSE NULL
                        END,

        success       = CASE
                          WHEN action = 'BUY' THEN
                            CASE
                              WHEN price_target IS NOT NULL
                              THEN CASE WHEN :price_exit >= price_target THEN 1 ELSE 0 END
                              WHEN ta35_at_entry IS NOT NULL AND :ta35_at_exit IS NOT NULL
                              THEN CASE WHEN :price_exit > price_entry AND
                                   ((:price_exit - price_entry) / price_entry) >
                                   ((:ta35_at_exit - ta35_at_entry) / ta35_at_entry)
                                   THEN 1 ELSE 0 END
                              ELSE CASE WHEN :price_exit > price_entry THEN 1 ELSE 0 END
                            END
                          WHEN action = 'SELL'
                          THEN CASE WHEN :price_exit < price_entry THEN 1 ELSE 0 END
                          ELSE NULL
                        END

    WHERE id = :rec_id
    """
    try:
        conn = get_connection()
        with conn:
            cur = conn.cursor()
            cur.execute(sql, {
                "rec_id": rec_id,
                "price_exit": _float(price_exit),
                "ta35_at_exit": _float(ta35_at_exit),
            })
        conn.close()
        logger.info("Position %s closed at %s", rec_id, price_exit)
    except Exception as e:
        logger.warning("close_position failed: %s", e)


def save_snapshot(
    rec_id: int,
    snapshot_days: int,
    price_now: float,
    ta35_now: float | None,
    ta35_at_rec: float | None,
    price_at_rec: float,
    action: str,
) -> None:
    """Compute and persist a single time-horizon snapshot for a recommendation.

    Args:
        rec_id: DB id of the parent recommendation.
        snapshot_days: Horizon — one of 7, 30, 60, 90.
        price_now: Current market price of the security.
        ta35_now: Current TA-35 index level (None if unavailable).
        ta35_at_rec: TA-35 level at the time the recommendation was made.
        price_at_rec: Security price at recommendation time.
        action: Recommendation action string (BUY/SELL/HOLD/WATCH/TRIM).
    """
    if not price_at_rec:
        logger.warning("save_snapshot: price_at_rec is zero for rec_id=%s, skipping", rec_id)
        return
    return_pct = (price_now - price_at_rec) / price_at_rec * 100

    ta35_return_pct: float | None = None
    alpha_pct: float | None = None
    if ta35_now is not None and ta35_at_rec is not None and ta35_at_rec > 0:
        ta35_return_pct = (ta35_now - ta35_at_rec) / ta35_at_rec * 100
        alpha_pct = return_pct - ta35_return_pct

    if action in ("BUY", "WATCH"):
        was_correct: int | None = 1 if return_pct > 0 else 0
    elif action in ("SELL", "TRIM"):
        was_correct = 1 if return_pct < 0 else 0
    else:
        was_correct = None

    sql = """
    INSERT INTO recommendation_snapshots
        (rec_id, snapshot_days, price_at_snapshot, ta35_at_snapshot, ta35_at_rec,
         return_pct, ta35_return_pct, alpha_pct, was_correct)
    VALUES
        (:rec_id, :snapshot_days, :price_at_snapshot, :ta35_at_snapshot,
         :ta35_at_rec, :return_pct, :ta35_return_pct, :alpha_pct, :was_correct)
    ON CONFLICT (rec_id, snapshot_days) DO NOTHING
    """
    try:
        conn = get_connection()
        with conn:
            cur = conn.cursor()
            cur.execute(sql, {
                "rec_id":            rec_id,
                "snapshot_days":     snapshot_days,
                "price_at_snapshot": round(price_now, 4),
                "ta35_at_snapshot":  round(ta35_now, 4) if ta35_now else None,
                "ta35_at_rec":       round(ta35_at_rec, 4) if ta35_at_rec else None,
                "return_pct":        round(return_pct, 4),
                "ta35_return_pct":   round(ta35_return_pct, 4) if ta35_return_pct else None,
                "alpha_pct":         round(alpha_pct, 4) if alpha_pct else None,
                "was_correct":       was_correct,
            })
        conn.close()
        logger.info("Snapshot saved: rec %s @ %dd = %.2f%%", rec_id, snapshot_days, return_pct)
    except Exception as e:
        logger.warning("save_snapshot failed: %s", e)


# ── Read operations ───────────────────────────────────────────────────────────

def get_decision_history(n_weeks: int = 4) -> list[dict]:
    """Fetch decided recommendations from the last n_weeks for Claude context injection.

    Returns approved recommendations and explicitly rejected ones (closed with a
    rejection note). Excludes HOLDs closed without a trade — they carry no signal
    about whether Claude's analytical direction was right or wrong.

    Args:
        n_weeks: How many weeks back to look. Defaults to 4 (last month of decisions).

    Returns:
        List of dicts with keys: symbol, action, approved, approval_note, created_at.
        Ordered newest-first.
    """
    days = n_weeks * 7
    # SQLite doesn't accept parameters inside datetime() modifiers — inject as a
    # trusted integer (n_weeks is always an int, no SQL injection risk).
    since_clause = f"datetime('now', '-{days} days')"
    sql = f"""
    SELECT symbol, action, approved, approval_note, created_at, price_limit
    FROM recommendations
    WHERE (
        approved = 1
        OR (approved = 0 AND closed = 1 AND approval_note NOT LIKE '%HOLD%')
    )
      AND created_at >= {since_clause}
    ORDER BY created_at DESC
    """
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(sql)
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception as exc:
        logger.warning("get_decision_history failed: %s", exc)
        return []


def get_open_approved_recs() -> list[dict]:
    """Return all approved, non-closed BUY/SELL recommendations.

    Returns:
        List of row dicts ordered by created_at descending.
    """
    sql = """
    SELECT id, symbol, action, price_entry, price_target,
           ta35_at_entry, actual_price, created_at
    FROM recommendations
    WHERE approved = 1
      AND closed   = 0
      AND action   IN ('BUY', 'SELL')
    ORDER BY created_at DESC
    """
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(sql)
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception as e:
        logger.warning("get_open_approved_recs failed: %s", e)
        return []


def get_pending_recs(limit: int = 20) -> list[dict]:
    """Return recommendations awaiting user approval.

    Args:
        limit: Maximum number of rows to return. Defaults to 20.

    Returns:
        List of row dicts ordered by created_at descending.
    """
    sql = """
    SELECT id, created_at, symbol, action, conviction,
           price_entry, price_target, thesis, key_risk
    FROM recommendations
    WHERE approved IS NULL
    ORDER BY created_at DESC
    LIMIT :limit
    """
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(sql, {"limit": limit})
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception as e:
        logger.warning("get_pending_recs failed: %s", e)
        return []


def get_performance_summary(days: int = 30) -> PerformanceSummary | None:
    """Build a full performance summary for injection into Claude's prompt.

    Args:
        days: Look-back window in days. Defaults to 30.

    Returns:
        A PerformanceSummary instance, or None if no data exists yet.
    """
    # SQLite doesn't accept parameters inside datetime() modifiers,
    # so inject days as a trusted integer directly into the SQL string.
    since_clause = f"datetime('now', '-{days} days')"
    sql = f"""
    SELECT
        COUNT(*)                                                              AS total_recs,
        SUM(CASE WHEN approved = 1 THEN 1 ELSE 0 END)                        AS approved_recs,
        SUM(CASE WHEN success  = 1 THEN 1 ELSE 0 END)                        AS successful_recs,
        SUM(CASE WHEN approved = 1 AND closed = 0 THEN 1 ELSE 0 END)         AS open_positions,

        ROUND(AVG(CASE WHEN return_pct    IS NOT NULL THEN return_pct    END), 2) AS avg_return,
        ROUND(AVG(CASE WHEN benchmark_pct IS NOT NULL THEN benchmark_pct END), 2) AS avg_benchmark,
        ROUND(AVG(CASE WHEN alpha         IS NOT NULL THEN alpha         END), 2) AS avg_alpha,

        ROUND(AVG(CASE
            WHEN approved = 1 AND closed = 0 AND price_entry > 0 AND price_current IS NOT NULL
            THEN (price_current - price_entry) / price_entry * 100
            END), 2) AS open_unrealized,

        MAX(return_pct)  AS best_return,
        MIN(return_pct)  AS worst_return

    FROM recommendations
    WHERE created_at >= {since_clause}
    """
    best_sql = f"""
    SELECT symbol || ' +' || ROUND(return_pct, 1) || '%' AS label
    FROM recommendations
    WHERE return_pct IS NOT NULL AND created_at >= {since_clause}
    ORDER BY return_pct DESC LIMIT 1
    """
    worst_sql = f"""
    SELECT symbol || ' ' || ROUND(return_pct, 1) || '%' AS label
    FROM recommendations
    WHERE return_pct IS NOT NULL AND created_at >= {since_clause}
    ORDER BY return_pct ASC LIMIT 1
    """
    conv_sql = f"""
    SELECT conviction,
           SUM(CASE WHEN approved = 1 THEN 1 ELSE 0 END) AS approved,
           SUM(CASE WHEN success  = 1 THEN 1 ELSE 0 END) AS won
    FROM recommendations
    WHERE created_at >= {since_clause}
    GROUP BY conviction
    """
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(sql)
        row = dict(cur.fetchone() or {})
        if not row or not row.get("total_recs"):
            conn.close()
            return None

        cur.execute(best_sql)
        best_row = cur.fetchone()
        cur.execute(worst_sql)
        worst_row = cur.fetchone()
        cur.execute(conv_sql)
        conv_rows = {r["conviction"]: dict(r) for r in cur.fetchall()}
        conn.close()

        def _rate(conviction: str) -> float:
            """Calculate success rate for a given conviction level."""
            c = conv_rows.get(conviction, {})
            approved = c.get("approved") or 0
            won = c.get("won") or 0
            return round(won / approved * 100, 1) if approved > 0 else 0.0

        total = row["total_recs"] or 0
        approved = row["approved_recs"] or 0
        success = row["successful_recs"] or 0
        rate = round(success / approved * 100, 1) if approved > 0 else 0.0

        return PerformanceSummary(
            period_days=days,
            total_recs=total,
            approved_recs=approved,
            successful_recs=success,
            success_rate=rate,
            avg_return_pct=float(row["avg_return"] or 0),
            avg_benchmark_pct=float(row["avg_benchmark"] or 0),
            avg_alpha=float(row["avg_alpha"] or 0),
            best_trade=best_row["label"] if best_row else None,
            worst_trade=worst_row["label"] if worst_row else None,
            high_conv_success=_rate("HIGH"),
            med_conv_success=_rate("MEDIUM"),
            low_conv_success=_rate("LOW"),
            open_positions=row["open_positions"] or 0,
            open_unrealized_pct=float(row["open_unrealized"] or 0),
        )
    except Exception as e:
        logger.warning("get_performance_summary failed: %s", e)
        return None


def get_recs_needing_snapshots() -> list[dict]:
    """Return all recommendations that have at least one overdue snapshot.

    Rewrites the PostgreSQL ARRAY_AGG/UNNEST approach in Python for SQLite
    compatibility.

    Returns:
        List of row dicts with keys: id, symbol, action, price_entry,
        ta35_at_entry, created_at, done_snapshots (list of ints).
    """
    horizons = [7, 30, 90]
    sql = """
    SELECT id, symbol, action, price_entry, ta35_at_entry, created_at
    FROM recommendations
    WHERE price_entry IS NOT NULL
    ORDER BY created_at DESC
    """
    snap_sql = """
    SELECT snapshot_days FROM recommendation_snapshots WHERE rec_id = ?
    """
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(sql)
        recs = [dict(r) for r in cur.fetchall()]

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        result = []
        for rec in recs:
            cur.execute(snap_sql, (rec["id"],))
            done = [r["snapshot_days"] for r in cur.fetchall()]
            created_at = datetime.fromisoformat(rec["created_at"])
            overdue = [
                d for d in horizons
                if now >= created_at + timedelta(days=d) and d not in done
            ]
            if overdue:
                rec["done_snapshots"] = done
                result.append(rec)

        conn.close()
        return result
    except Exception as e:
        logger.warning("get_recs_needing_snapshots failed: %s", e)
        return []


def get_snapshot_scorecard() -> list[dict]:
    """Return per-horizon hit rates and average returns from all snapshots.

    Returns:
        List of dicts ordered by snapshot_days, each with keys:
        snapshot_days, total, correct, hit_rate, avg_return, avg_alpha.
    """
    sql = """
    SELECT
        snapshot_days,
        COUNT(*)                                          AS total,
        SUM(CASE WHEN was_correct = 1 THEN 1 ELSE 0 END) AS correct,
        ROUND(
            CAST(SUM(CASE WHEN was_correct = 1 THEN 1 ELSE 0 END) AS REAL)
            / NULLIF(COUNT(*), 0) * 100, 1
        )                                                 AS hit_rate,
        ROUND(AVG(return_pct), 2)                         AS avg_return,
        ROUND(AVG(alpha_pct),  2)                         AS avg_alpha
    FROM recommendation_snapshots
    GROUP BY snapshot_days
    ORDER BY snapshot_days
    """
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(sql)
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception as e:
        logger.warning("get_snapshot_scorecard failed: %s", e)
        return []


# ── Batch queries ─────────────────────────────────────────────────────────────

def get_latest_batch() -> tuple[str, list[dict]]:
    """Return the most recent recommendation batch as (batch_prefix, rows).

    Finds the MAX(created_at) timestamp across all recommendations, then
    fetches all rows whose created_at matches that timestamp to the minute
    (i.e. the same batch run).

    Returns:
        A tuple of (batch_prefix, rows) where batch_prefix is the first 16
        characters of the latest created_at (``YYYY-MM-DD HH:MM``) and rows
        is a list of dicts for each recommendation in that batch, ordered by
        id ASC. Returns ("", []) if the table is empty or on any error.
    """
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT MAX(created_at) FROM recommendations")
        latest = cur.fetchone()[0]
        if not latest:
            conn.close()
            return "", []
        batch_prefix = latest[:16]
        cur.execute(
            "SELECT * FROM recommendations WHERE created_at LIKE ? ORDER BY id ASC",
            (f"{batch_prefix}%",),
        )
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return batch_prefix, rows
    except Exception as exc:
        logger.warning("get_latest_batch failed: %s", exc)
        return "", []


# ── Prompt formatting ─────────────────────────────────────────────────────────

def format_for_prompt(summary: PerformanceSummary | None) -> str:
    """Format a PerformanceSummary as a plain-text block for Claude's prompt.

    Args:
        summary: The summary to format, or None for a first-run placeholder.

    Returns:
        A multi-line string ready to be embedded in the agent prompt.
    """
    if not summary:
        return "No performance history yet."

    lines = [
        f"=== Agent Performance ({summary.period_days}d) ===",
        f"Recommendations: {summary.total_recs} made | {summary.approved_recs} approved | {summary.successful_recs} successful ({summary.success_rate}%)",
        f"Returns: avg {summary.avg_return_pct:+.1f}% | benchmark {summary.avg_benchmark_pct:+.1f}% | alpha {summary.avg_alpha:+.1f}%",
    ]
    if summary.open_positions:
        lines.append(f"Open positions: {summary.open_positions} | unrealized {summary.open_unrealized_pct:+.1f}%")
    if summary.best_trade:
        lines.append(f"Best: {summary.best_trade}")
    if summary.worst_trade:
        lines.append(f"Worst: {summary.worst_trade}")
    lines.append(f"Conviction accuracy — HIGH: {summary.high_conv_success}% | MED: {summary.med_conv_success}% | LOW: {summary.low_conv_success}%")
    if summary.top_pattern:
        lines.append(f"Best pattern: {summary.top_pattern}")
    return "\n".join(lines)
