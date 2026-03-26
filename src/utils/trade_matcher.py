"""Auto-matches executed trades from Bank Discount history to approved recommendations.

Reads approved recommendations that have not yet been execution-logged, then
matches them against recently executed transactions by security ID and action
direction. Writes price_actual, qty_actual, and executed_at back to the DB.
"""

import logging
from datetime import datetime, timedelta
from decimal import Decimal

from src.db.recommendations_db import get_connection

logger = logging.getLogger(__name__)

# Map Bank Discount Hebrew transaction types to canonical BUY/SELL strings.
_TX_TYPE_MAP: dict[str, str] = {
    "קניה":  "BUY",
    "מכירה": "SELL",
}


def _normalize_tx_action(tx_type: str) -> str:
    """Normalize a Hebrew Bank Discount transaction type to BUY or SELL.

    Args:
        tx_type: Raw transaction_type string from the Bank Discount export
            (e.g. "קניה", "מכירה").

    Returns:
        "BUY", "SELL", or the uppercased raw value if no mapping exists.
    """
    for heb, eng in _TX_TYPE_MAP.items():
        if heb in tx_type:
            return eng
    return tx_type.strip().upper()


def match_and_log_trades(
    transactions: list[dict],
    lookback_days: int = 14,
) -> dict:
    """Match recently executed transactions to approved recommendations.

    Matching rules:
    - Symbol: tx security_id == rec symbol
    - Action: BUY == BUY, SELL == SELL, TRIM rec also matches a SELL tx
    - Only approved recs where price_actual IS NULL (not yet execution-logged)
    - Only transactions whose execution_date is within the last lookback_days
    - Most recent approved rec wins if multiple candidates match
    - Each recommendation is matched at most once per call

    On each match, writes price_actual, qty_actual, and executed_at.  Also
    sets approval_note to a standard auto-match string if the note is currently
    NULL or empty — existing notes are never overwritten.

    Args:
        transactions: List of transaction dicts as returned by
            parse_transaction_history().  Keys used: security_id,
            transaction_type, execution_price, quantity, execution_date.
        lookback_days: How far back (in calendar days) to accept transactions.
            Defaults to 14.

    Returns:
        Dict with keys:
        - matched (int): number of recommendations updated
        - skipped (int): transactions that could not be matched
        - log_lines (list[str]): human-readable match descriptions
    """
    if not transactions:
        return {"matched": 0, "skipped": 0, "log_lines": []}

    cutoff = (datetime.now() - timedelta(days=lookback_days)).date()
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, symbol, action, price_entry, created_at
        FROM recommendations
        WHERE approved = 1
          AND price_actual IS NULL
        ORDER BY created_at DESC
    """)
    approved = [
        dict(zip(["id", "symbol", "action", "price_entry", "created_at"], r))
        for r in cur.fetchall()
    ]

    matched = 0
    skipped = 0
    log_lines: list[str] = []
    used_ids: set[int] = set()

    for tx in transactions:
        # Resolve execution date from datetime object or parse from string
        tx_date = None
        raw_date = tx.get("execution_date")
        if isinstance(raw_date, datetime):
            tx_date = raw_date.date()
        elif raw_date is not None:
            for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
                try:
                    tx_date = datetime.strptime(
                        str(raw_date).split("T")[0], fmt
                    ).date()
                    break
                except ValueError:
                    continue

        if tx_date is None or tx_date < cutoff:
            skipped += 1
            continue

        tx_symbol = str(tx.get("security_id", "")).strip()
        tx_action = _normalize_tx_action(str(tx.get("transaction_type", "")))

        # execution_price and quantity are Decimal from the parser
        raw_price = tx.get("execution_price")
        raw_qty = tx.get("quantity")
        tx_price = float(raw_price) if raw_price is not None else None
        tx_qty = float(abs(raw_qty)) if raw_qty is not None else None

        if not all([tx_symbol, tx_action, tx_price, tx_qty]):
            skipped += 1
            continue

        # Find the best (most-recent) unmatched approved rec
        best: dict | None = None
        for rec in approved:
            if rec["id"] in used_ids:
                continue
            sym_ok = str(rec["symbol"]).strip() == tx_symbol
            act_ok = (
                rec["action"].upper() == tx_action
                or (rec["action"].upper() == "TRIM" and tx_action == "SELL")
            )
            if sym_ok and act_ok:
                best = rec
                break

        if not best:
            skipped += 1
            continue

        auto_note = (
            f"Auto-matched \u2014 {best['action']} {int(tx_qty)} units"
            f" @ \u20aa{tx_price:,.2f} on {tx_date} (trade matcher)"
        )
        cur.execute(
            """
            UPDATE recommendations
            SET price_actual   = ?,
                qty_actual     = ?,
                executed_at    = ?,
                approval_note  = CASE
                    WHEN approval_note IS NULL OR approval_note = ''
                    THEN ?
                    ELSE approval_note
                END
            WHERE id = ?
            """,
            (tx_price, tx_qty, tx_date.isoformat(), auto_note, best["id"]),
        )

        used_ids.add(best["id"])
        matched += 1
        log_lines.append(
            f"Auto-logged rec #{best['id']}: {best['action']} {tx_symbol} "
            f"@ \u20aa{tx_price:,.2f} \u00d7 {int(tx_qty)} units ({tx_date})"
        )
        logger.info(
            "Trade matched: rec %s (%s %s) \u2190 tx %s @ %.4f x %d on %s",
            best["id"],
            best["action"],
            tx_symbol,
            tx_action,
            tx_price,
            int(tx_qty),
            tx_date,
        )

    conn.commit()
    conn.close()
    return {"matched": matched, "skipped": skipped, "log_lines": log_lines}
