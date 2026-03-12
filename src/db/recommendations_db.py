"""Production-grade feedback storage for the portfolio agent."""

import logging
import os
from datetime import datetime, timedelta
from decimal import Decimal

import psycopg2
import psycopg2.extras
import psycopg2.extensions

from src.models.recommendation import (
    ApprovalUpdate,
    OutcomeUpdate,
    PerformanceSummary,
    RecommendationRecord,
)

logger = logging.getLogger(__name__)


# ── Connection ────────────────────────────────────────────────────────────────

def _get_conn() -> psycopg2.extensions.connection:
    """Open and return a new psycopg2 connection using env-var credentials."""
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 5432)),
        dbname=os.getenv("DB_NAME", "portfolio_agent"),
        user=os.getenv("DB_USER", "agent_admin"),
        password=os.getenv("DB_PASSWORD", "localdev123"),
        cursor_factory=psycopg2.extras.RealDictCursor,
    )


# ── Schema ────────────────────────────────────────────────────────────────────

def init_recommendations_table() -> None:
    """Create or migrate the recommendations table and its indexes.

    Safe to call on every startup — uses CREATE TABLE IF NOT EXISTS.
    Schema tracks the full lifecycle: recommendation → approval →
    mark-to-market → close → performance metrics.
    """
    sql = """
    CREATE TABLE IF NOT EXISTS recommendations (
        id               SERIAL PRIMARY KEY,
        created_at       TIMESTAMPTZ  DEFAULT NOW(),

        -- What Claude recommended
        symbol           VARCHAR(30)  NOT NULL,
        action           VARCHAR(10)  NOT NULL,   -- BUY/SELL/HOLD/WATCH
        conviction       VARCHAR(10)  NOT NULL,   -- HIGH/MEDIUM/LOW
        thesis           TEXT         NOT NULL,
        key_risk         TEXT         NOT NULL,
        run_type         VARCHAR(20)  DEFAULT 'morning',

        -- Prices at recommendation time
        price_entry      DECIMAL(14,4),           -- asset price when rec was made
        price_target     DECIMAL(14,4),
        ta35_at_entry    DECIMAL(14,4),           -- TA-35 level at same moment

        -- User decision
        approved         BOOLEAN,                 -- NULL=pending, TRUE=yes, FALSE=no
        actual_price     DECIMAL(14,4),           -- price user actually traded at
        quantity         INTEGER,                 -- units bought/sold
        approval_note    TEXT,
        approved_at      TIMESTAMPTZ,

        -- Mark-to-market (auto-updated by price_updater.py)
        price_current    DECIMAL(14,4),
        ta35_current     DECIMAL(14,4),
        last_updated     TIMESTAMPTZ,

        -- Outcome (filled when position is closed)
        closed           BOOLEAN      DEFAULT FALSE,
        price_exit       DECIMAL(14,4),
        exit_date        TIMESTAMPTZ,
        holding_days     INTEGER,

        -- Performance metrics (calculated on close)
        return_pct       DECIMAL(8,4),            -- (exit - entry) / entry * 100
        benchmark_pct    DECIMAL(8,4),            -- TA-35 return over same period
        alpha            DECIMAL(8,4),            -- return_pct - benchmark_pct
        success          BOOLEAN                  -- beat benchmark or hit target
    );

    CREATE INDEX IF NOT EXISTS idx_rec_symbol     ON recommendations(symbol);
    CREATE INDEX IF NOT EXISTS idx_rec_created_at ON recommendations(created_at);
    CREATE INDEX IF NOT EXISTS idx_rec_approved   ON recommendations(approved);
    CREATE INDEX IF NOT EXISTS idx_rec_closed     ON recommendations(closed);
    CREATE INDEX IF NOT EXISTS idx_rec_action     ON recommendations(action);
    CREATE INDEX IF NOT EXISTS idx_rec_conviction ON recommendations(conviction);
    """
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
            conn.commit()
        logger.info("recommendations table ready")
    except Exception as e:
        logger.error("init_recommendations_table failed: %s", e)


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
        (%(symbol)s, %(action)s, %(conviction)s, %(thesis)s, %(key_risk)s,
         %(price_entry)s, %(price_target)s, %(ta35_at_entry)s, %(run_type)s)
    RETURNING id
    """
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, rec.model_dump())
                row = cur.fetchone()
            conn.commit()
        return row["id"]
    except Exception as e:
        logger.error("save_recommendation failed: %s", e)
        return None


def update_approval(update: ApprovalUpdate) -> None:
    """Record the user's approval or rejection decision for a recommendation.

    Args:
        update: ApprovalUpdate containing rec_id, approved flag, and optional
            actual_price, quantity, and note fields.
    """
    sql = """
    UPDATE recommendations
    SET approved      = %(approved)s,
        actual_price  = %(actual_price)s,
        quantity      = %(quantity)s,
        approval_note = %(note)s,
        approved_at   = NOW()
    WHERE id = %(rec_id)s
    """
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, update.model_dump())
            conn.commit()
    except Exception as e:
        logger.error("update_approval failed: %s", e)


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
    SET price_current = %(price_current)s,
        ta35_current  = %(ta35_current)s,
        last_updated  = NOW()
    WHERE id = %(rec_id)s
      AND closed = FALSE
    """
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, {
                    "rec_id": rec_id,
                    "price_current": price_current,
                    "ta35_current": ta35_current,
                })
            conn.commit()
    except Exception as e:
        logger.error("update_mark_to_market failed: %s", e)


def close_position(
    rec_id: int,
    price_exit: Decimal,
    ta35_at_exit: Decimal | None = None,
) -> None:
    """Close a position and calculate return, benchmark, alpha, and success flag.

    All performance metrics are computed in SQL at close time. Success is
    defined as beating the TA-35 benchmark (or hitting the price target for BUY).

    Args:
        rec_id: DB id of the recommendation to close.
        price_exit: The price at which the position was exited.
        ta35_at_exit: TA-35 level at exit for benchmark comparison.
    """
    sql = """
    UPDATE recommendations
    SET closed        = TRUE,
        price_exit    = %(price_exit)s,
        exit_date     = NOW(),
        holding_days  = EXTRACT(DAY FROM NOW() - created_at)::INTEGER,
        ta35_current  = %(ta35_at_exit)s,

        return_pct    = CASE
                          WHEN price_entry IS NOT NULL AND price_entry > 0
                          THEN ROUND(((%(price_exit)s - price_entry)
                                      / price_entry * 100)::numeric, 4)
                          ELSE NULL
                        END,

        benchmark_pct = CASE
                          WHEN ta35_at_entry IS NOT NULL AND ta35_at_entry > 0
                               AND %(ta35_at_exit)s IS NOT NULL
                          THEN ROUND(((%(ta35_at_exit)s - ta35_at_entry)
                                      / ta35_at_entry * 100)::numeric, 4)
                          ELSE NULL
                        END,

        alpha         = CASE
                          WHEN ta35_at_entry IS NOT NULL AND ta35_at_entry > 0
                               AND %(ta35_at_exit)s IS NOT NULL
                               AND price_entry IS NOT NULL AND price_entry > 0
                          THEN ROUND((
                              ((%(price_exit)s - price_entry) / price_entry * 100) -
                              ((%(ta35_at_exit)s - ta35_at_entry) / ta35_at_entry * 100)
                          )::numeric, 4)
                          ELSE NULL
                        END,

        success       = CASE
                          WHEN action = 'BUY' THEN
                            CASE
                              WHEN price_target IS NOT NULL
                              THEN %(price_exit)s >= price_target
                              WHEN ta35_at_entry IS NOT NULL AND %(ta35_at_exit)s IS NOT NULL
                              THEN %(price_exit)s > price_entry AND
                                   ((%(price_exit)s - price_entry) / price_entry) >
                                   ((%(ta35_at_exit)s - ta35_at_entry) / ta35_at_entry)
                              ELSE %(price_exit)s > price_entry
                            END
                          WHEN action = 'SELL' THEN %(price_exit)s < price_entry
                          ELSE NULL
                        END

    WHERE id = %(rec_id)s
    """
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, {
                    "rec_id": rec_id,
                    "price_exit": price_exit,
                    "ta35_at_exit": ta35_at_exit,
                })
            conn.commit()
        logger.info("Position %s closed at %s", rec_id, price_exit)
    except Exception as e:
        logger.error("close_position failed: %s", e)


# ── Read operations ───────────────────────────────────────────────────────────

def get_open_approved_recs() -> list[dict]:
    """Return all approved, non-closed BUY/SELL recommendations.

    Used by price_updater.py to determine which positions to track.

    Returns:
        List of row dicts ordered by created_at descending.
    """
    sql = """
    SELECT id, symbol, action, price_entry, price_target,
           ta35_at_entry, actual_price, created_at
    FROM recommendations
    WHERE approved = TRUE
      AND closed   = FALSE
      AND action   IN ('BUY', 'SELL')
    ORDER BY created_at DESC
    """
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.error("get_open_approved_recs failed: %s", e)
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
    LIMIT %(limit)s
    """
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, {"limit": limit})
                return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.error("get_pending_recs failed: %s", e)
        return []


def get_performance_summary(days: int = 30) -> PerformanceSummary | None:
    """Build a full performance summary for injection into Claude's prompt.

    Aggregates return, benchmark, alpha, and per-conviction success rates
    for all recommendations created within the last `days` days.

    Args:
        days: Look-back window in days. Defaults to 30.

    Returns:
        A PerformanceSummary instance, or None if no data exists yet.
    """
    sql = """
    WITH base AS (
        SELECT *
        FROM recommendations
        WHERE created_at >= NOW() - INTERVAL '%(days)s days'
    ),
    conviction_stats AS (
        SELECT
            conviction,
            COUNT(*) FILTER (WHERE approved = TRUE)                   AS approved,
            COUNT(*) FILTER (WHERE success = TRUE)                     AS won
        FROM base
        GROUP BY conviction
    )
    SELECT
        COUNT(*)                                                        AS total_recs,
        COUNT(*) FILTER (WHERE approved = TRUE)                        AS approved_recs,
        COUNT(*) FILTER (WHERE success = TRUE)                         AS successful_recs,
        COUNT(*) FILTER (WHERE approved = TRUE AND closed = FALSE)     AS open_positions,

        ROUND(AVG(return_pct)    FILTER (WHERE return_pct IS NOT NULL), 2)    AS avg_return,
        ROUND(AVG(benchmark_pct) FILTER (WHERE benchmark_pct IS NOT NULL), 2) AS avg_benchmark,
        ROUND(AVG(alpha)         FILTER (WHERE alpha IS NOT NULL), 2)         AS avg_alpha,

        ROUND(AVG(CASE WHEN price_entry > 0 AND price_current IS NOT NULL AND closed = FALSE
                  THEN (price_current - price_entry) / price_entry * 100
                  ELSE NULL END), 2)                                           AS open_unrealized,

        MAX(return_pct)  AS best_return,
        MIN(return_pct)  AS worst_return,

        (SELECT symbol || ' +' || ROUND(return_pct,1)::text || '%%'
         FROM base WHERE return_pct IS NOT NULL
         ORDER BY return_pct DESC LIMIT 1)                             AS best_trade,
        (SELECT symbol || ' ' || ROUND(return_pct,1)::text || '%%'
         FROM base WHERE return_pct IS NOT NULL
         ORDER BY return_pct ASC LIMIT 1)                              AS worst_trade
    FROM base
    """
    conv_sql = """
    SELECT conviction,
           COUNT(*) FILTER (WHERE approved = TRUE)   AS approved,
           COUNT(*) FILTER (WHERE success = TRUE)     AS won
    FROM recommendations
    WHERE created_at >= NOW() - INTERVAL '%(days)s days'
    GROUP BY conviction
    """
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, {"days": days})
                row = cur.fetchone()
                cur.execute(conv_sql, {"days": days})
                conv_rows = {r["conviction"]: r for r in cur.fetchall()}

        if not row or row["total_recs"] == 0:
            return None

        def _rate(conviction: str) -> float:
            """Calculate success rate for a given conviction level."""
            conv = conv_rows.get(conviction, {})
            approved = conv.get("approved", 0) or 0
            won = conv.get("won", 0) or 0
            return round(won / approved * 100, 1) if approved > 0 else 0.0

        total = row["total_recs"]
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
            best_trade=row["best_trade"],
            worst_trade=row["worst_trade"],
            high_conv_success=_rate("HIGH"),
            med_conv_success=_rate("MEDIUM"),
            low_conv_success=_rate("LOW"),
            open_positions=row["open_positions"] or 0,
            open_unrealized_pct=float(row["open_unrealized"] or 0),
        )
    except Exception as e:
        logger.error("get_performance_summary failed: %s", e)
        return None


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
