"""
src/price_updater.py
====================================
Runs automatically on every agent cycle (morning + afternoon).
For every approved, open recommendation it:
  1. Fetches the current price from Yahoo Finance
  2. Fetches the current TA-35 level
  3. Updates mark-to-market in the DB
  4. Prints a summary of open positions

Also called by approve.py when closing a position,
to get the exit price automatically.

update_all_prices() scores ALL recommendations regardless of approval status,
populating current_price, current_return_pct, net_return_pct, direction_correct,
transaction_cost_pct, and last_scored_at for the Quality Tracker panel.

Usage (standalone):
    python -m src.price_updater
"""

import logging
from datetime import datetime
from decimal import Decimal
from typing import Optional

logger = logging.getLogger(__name__)

# One-way transaction cost per action type (as % of position value).
TRANSACTION_COSTS: dict[str, float] = {
    "BUY":   0.30,
    "SELL":  0.30,
    "TRIM":  0.15,
    "HOLD":  0.00,
    "WATCH": 0.00,
}

# Annual management fee (%) by TASE fund security ID.
ANNUAL_FEES: dict[str, float] = {
    "5136544": 0.16,   # מיטב כספית שקלית כשרה
    "5142088": 0.20,   # קסם KTF ביטחוניות
    "1235985": 0.30,   # תכלית סל ביטחוניות
    "5141882": 0.35,   # תכלית TTF ביטחוניות
    "5109418": 0.30,   # תכלית TTF ת"א 35
    "5134556": 0.25,   # תכלית TTF Semiconductor (estimate)
    "5130661": 0.00,   # הראל מחקה ת"א 35
    "1148907": 0.25,   # הראל סל ת"א 35
}


# ── Price fetching ────────────────────────────────────────────────────────────

def fetch_price_yahoo(symbol: str) -> Optional[Decimal]:
    """
    Fetches latest price from Yahoo Finance.
    For TASE stocks appends .TA suffix if not already there.
    For FUND- prefixed symbols returns None (funds use funder.co.il).
    """
    if symbol.startswith("FUND-"):
        return _fetch_fund_nav(symbol)

    # Normalize TASE ticker
    yf_symbol = symbol if "." in symbol else f"{symbol}.TA"
    try:
        import yfinance as yf
        ticker = yf.Ticker(yf_symbol)
        hist = ticker.history(period="2d")
        if hist.empty:
            logger.warning(f"No data from Yahoo for {yf_symbol}")
            return None
        price = Decimal(str(round(float(hist["Close"].iloc[-1]), 4)))
        return price
    except ImportError:
        # yfinance not installed — use requests fallback
        return _fetch_price_requests(yf_symbol)
    except Exception as e:
        logger.warning(f"Yahoo fetch failed for {yf_symbol}: {e}")
        return None


def _fetch_price_requests(yf_symbol: str) -> Optional[Decimal]:
    """Fallback price fetch using requests (no yfinance dependency)."""
    try:
        import requests
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yf_symbol}"
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=10)
        data = r.json()
        result = data.get("chart", {}).get("result", [])
        if not result:
            return None
        meta = result[0].get("meta", {})
        price = meta.get("regularMarketPrice") or meta.get("previousClose")
        if price:
            return Decimal(str(round(float(price), 4)))
    except Exception as e:
        logger.warning(f"Requests fallback failed for {yf_symbol}: {e}")
    return None


def _fetch_fund_nav(fund_symbol: str) -> Optional[Decimal]:
    """
    Fetches fund NAV from funder.co.il.
    fund_symbol format: FUND-5136544
    Returns NAV in shekels (already ÷100).
    """
    try:
        from src.funds_connector import get_all_funds
        fund_id_str = fund_symbol.replace("FUND-", "")
        funds = get_all_funds()
        # Keys may be int or str depending on the connector — try both
        data = funds.get(int(fund_id_str)) or funds.get(fund_id_str)
        if data and data.get("nav") is not None:
            return Decimal(str(round(float(data["nav"]) / 100, 4)))
    except Exception as e:
        logger.warning(f"Fund NAV fetch failed for {fund_symbol}: {e}")
    return None


def fetch_ta35() -> Optional[Decimal]:
    """Fetches the current TA-35 index level from Yahoo Finance (TA35.TA)."""
    try:
        import yfinance as yf
        ticker = yf.Ticker("TA35.TA")
        hist = ticker.history(period="2d")
        if not hist.empty:
            return Decimal(str(round(float(hist["Close"].iloc[-1]), 2)))
    except Exception:
        pass
    # Fallback via requests
    try:
        import requests
        url = "https://query1.finance.yahoo.com/v8/finance/chart/TA35.TA"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        data = r.json()
        result = data.get("chart", {}).get("result", [])
        if result:
            meta = result[0].get("meta", {})
            price = meta.get("regularMarketPrice") or meta.get("previousClose")
            if price:
                return Decimal(str(round(float(price), 2)))
    except Exception as e:
        logger.warning(f"TA-35 fetch failed: {e}")
    return None


# ── Main updater logic ────────────────────────────────────────────────────────

def run_price_update(verbose: bool = True) -> dict:
    """
    Fetches current prices for all open approved recommendations.
    Updates mark-to-market in the DB.
    Returns summary dict.
    """
    from src.db.recommendations_db import get_open_approved_recs, update_mark_to_market

    open_recs = get_open_approved_recs()
    if not open_recs:
        if verbose:
            print("  📊 No open positions to update")
        return {"updated": 0, "failed": 0, "positions": []}

    # Fetch TA-35 once for the whole run
    ta35 = fetch_ta35()
    if verbose:
        print(f"  📈 TA-35: ₪{ta35:,.2f}" if ta35 else "  ⚠️  TA-35 fetch failed")

    results = []
    updated = 0
    failed  = 0

    for rec in open_recs:
        symbol       = rec["symbol"]
        price_entry  = rec.get("actual_price") or rec.get("price_entry")
        price_target = rec.get("price_target")

        current = fetch_price_yahoo(symbol)
        if current is None:
            if verbose:
                print(f"  ⚠️  {symbol}: price fetch failed")
            failed += 1
            continue

        # Calculate unrealized P&L
        unrealized_pct = None
        if price_entry and float(price_entry) > 0:
            unrealized_pct = round(
                (float(current) - float(price_entry)) / float(price_entry) * 100, 2
            )

        # Check if target hit
        target_hit = False
        if price_target:
            if rec["action"] == "BUY"  and current >= price_target:
                target_hit = True
            if rec["action"] == "SELL" and current <= price_target:
                target_hit = True

        update_mark_to_market(rec["id"], current, ta35)
        updated += 1

        result = {
            "id":             rec["id"],
            "symbol":         symbol,
            "action":         rec["action"],
            "price_entry":    price_entry,
            "price_current":  current,
            "unrealized_pct": unrealized_pct,
            "target_hit":     target_hit,
            "holding_days":   (datetime.now() - rec["created_at"].replace(tzinfo=None)).days
                              if rec.get("created_at") else None,
        }
        results.append(result)

        if verbose:
            arrow  = "📈" if (unrealized_pct or 0) >= 0 else "📉"
            target_str = " 🎯 TARGET HIT" if target_hit else ""
            pnl_str = f"{unrealized_pct:+.2f}%" if unrealized_pct is not None else "N/A"
            days_str = f"{result['holding_days']}d" if result['holding_days'] is not None else ""
            print(f"  {arrow} [{rec['id']:>4}] {symbol:<20} ₪{current:<10.4f} {pnl_str:>8}  {days_str}{target_str}")

    return {"updated": updated, "failed": failed, "positions": results}


def update_fund_prices_from_portfolio(holdings: list) -> int:
    """Update current prices for fund recs using the latest portfolio upload data.

    Fund tickers (numeric IDs) cannot be fetched from yfinance — this function
    uses the Bank Discount upload's current NAV prices (already in the portfolio
    snapshot) so the Quality Tracker can score fund recs too.

    Args:
        holdings: List of Holding objects from the current portfolio snapshot.

    Returns:
        Number of fund recommendation rows updated.
    """
    from src.db.recommendations_db import get_connection

    if not holdings:
        return 0

    # Build symbol → current_price map for fund holdings only
    price_map: dict[str, float] = {
        str(h.ticker): float(h.current_price)
        for h in holdings
        if str(h.ticker).isdigit()
    }
    if not price_map:
        return 0

    conn = get_connection()
    cur = conn.cursor()
    updated = 0
    now_str = datetime.utcnow().isoformat()

    for symbol, current_price in price_map.items():
        cur.execute(
            """SELECT id, action, price_entry, created_at
               FROM recommendations
               WHERE symbol = ?
                 AND price_entry IS NOT NULL AND price_entry > 0
                 AND action NOT IN ('WATCH')""",
            (symbol,),
        )
        for row in cur.fetchall():
            rec_id, action, entry_price, created_at = row
            entry_f = float(entry_price)
            gross_return = round((current_price - entry_f) / entry_f * 100, 4)

            # Transaction cost: one-way fee + prorated annual fund fee
            tx_cost = TRANSACTION_COSTS.get(action, 0.0)
            ann_fee = ANNUAL_FEES.get(symbol, 0.0)
            if ann_fee > 0 and created_at:
                try:
                    days_held = max((datetime.utcnow() - datetime.fromisoformat(created_at)).days, 0)
                    tx_cost += ann_fee * days_held / 365.0
                except (ValueError, TypeError):
                    pass
            tx_cost = round(tx_cost, 4)
            net_return = round(gross_return - tx_cost, 4)
            direction = score_direction(action, entry_f, current_price)

            conn.execute(
                """UPDATE recommendations
                   SET current_price        = ?,
                       current_return_pct   = ?,
                       net_return_pct       = ?,
                       direction_correct    = ?,
                       transaction_cost_pct = ?,
                       last_scored_at       = ?
                   WHERE id = ?""",
                (
                    round(current_price, 4),
                    gross_return,
                    net_return,
                    direction,
                    tx_cost,
                    now_str,
                    rec_id,
                ),
            )
            updated += 1

    conn.commit()
    conn.close()
    logger.info("update_fund_prices_from_portfolio: updated %d recs", updated)
    return updated


# ── Quality scorer helpers ────────────────────────────────────────────────────

def get_instrument_type(symbol: str) -> str:
    """Return 'fund' if the symbol is a numeric TASE security ID, else 'stock'.

    Args:
        symbol: Recommendation symbol string (e.g. "ESLT" or "5142088").

    Returns:
        'fund' for all-digit symbols, 'stock' otherwise.
    """
    return "fund" if symbol.isdigit() else "stock"


# Symbols that need an explicit Yahoo Finance ticker (not the default {symbol}.TA).
# Add entries here when a stock's TASE ticker doesn't resolve on Yahoo Finance.
SYMBOL_OVERRIDES: dict[str, str] = {
    "DELT": "DELT.TA",   # delisted — keep as placeholder; will still return None
    "ONE": "ONE.TA",
    "NVMI": "NVMI.TA",
}


def fetch_current_price(symbol: str) -> Optional[Decimal]:
    """Fetch the latest price for any symbol — stock via Yahoo, fund via NAV.

    Resolution order for stock symbols:
      1. SYMBOL_OVERRIDES exact ticker (if mapped)
      2. {symbol}.TA  (standard TASE suffix)
      3. bare symbol  (Nasdaq-listed Israeli stocks)

    Args:
        symbol: TASE ticker or numeric fund ID.

    Returns:
        Current price as Decimal, or None if unavailable.
    """
    if symbol.isdigit():
        return _fetch_fund_nav(f"FUND-{symbol}")

    # Try override first, then .TA, then bare symbol (deduplicated, order preserved)
    seen: set[str] = set()
    candidates: list[str] = []
    for candidate in [
        SYMBOL_OVERRIDES.get(symbol),   # override (may be None)
        f"{symbol}.TA",                  # standard TASE suffix
        symbol,                          # bare symbol (Nasdaq-listed stocks)
    ]:
        if candidate and candidate not in seen:
            candidates.append(candidate)
            seen.add(candidate)

    import yfinance as yf  # noqa: PLC0415

    for yf_symbol in candidates:
        try:
            hist = yf.Ticker(yf_symbol).history(period="2d")
            if not hist.empty:
                return Decimal(str(round(float(hist["Close"].iloc[-1]), 4)))
        except Exception as exc:  # noqa: BLE001
            logger.debug("fetch_current_price: %s failed: %s", yf_symbol, exc)

    logger.warning("fetch_current_price: no price found for %s (tried %s)", symbol, candidates)
    return None


def score_direction(action: str, price_entry: float, price_current: float) -> Optional[int]:
    """Determine whether a recommendation's directional call was correct.

    Scoring rules:
      BUY / TRIM  — correct if price rose, wrong if it fell.
      SELL        — correct if price fell, wrong if it rose.
      HOLD        — correct if price change is > -2% (flat or up = right call);
                    wrong if price dropped > 2% (should have sold).
      WATCH       — never scored (no directional prediction).

    Args:
        action: Recommendation action — BUY, SELL, TRIM, HOLD, or WATCH.
        price_entry: Price at recommendation time.
        price_current: Current market price.

    Returns:
        1 if direction was correct, 0 if wrong, None for WATCH or
        when price_entry is zero (cost basis unknown).
    """
    if price_entry <= 0:
        return None
    change_pct = (price_current - price_entry) / price_entry * 100
    if action == "HOLD":
        # Correct if price is flat or up (held was fine); wrong if drops > 2%
        return 0 if change_pct < -2.0 else 1
    if action == "WATCH":
        # Hypothetical BUY — correct if price did NOT rise (right to hold back)
        return 1 if change_pct <= 0 else 0
    moved_up = price_current > price_entry
    if action == "BUY":
        return 1 if moved_up else 0
    if action in ("SELL", "TRIM"):
        return 1 if not moved_up else 0
    return None


def update_all_prices(verbose: bool = True) -> dict:
    """Score ALL recommendations regardless of approval status.

    For each recommendation that has a price_entry, fetches the current
    market price and writes quality-scoring fields back to the DB:
      - current_price
      - current_return_pct  (raw % move since recommendation)
      - net_return_pct      (current_return_pct minus transaction cost)
      - direction_correct   (1=correct, 0=wrong, NULL=HOLD/WATCH)
      - transaction_cost_pct
      - last_scored_at

    Args:
        verbose: If True, prints per-row progress to stdout.

    Returns:
        Dict with keys: scored (int), failed (int), skipped (int).
    """
    from src.db.recommendations_db import get_connection

    # ── Phase 0: seed entry prices for new stock recs that have none ──────────
    # Stocks recommended as BUY/SELL but not yet held have no entry price from
    # the portfolio upload. Fetch today's price as a baseline so direction
    # scoring can start from this run forward.
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT id, symbol, action FROM recommendations "
            "WHERE price_entry IS NULL "
            "AND symbol NOT GLOB '[0-9]*'"   # stocks only — funds use portfolio upload
        )
        no_entry = cur.fetchall()
        seeded = 0
        for row in no_entry:
            rec_id, symbol, action = row["id"], row["symbol"], row["action"]
            price = fetch_current_price(symbol)
            if price:
                conn.execute(
                    "UPDATE recommendations SET price_entry = ? WHERE id = ?",
                    (float(price), rec_id),
                )
                seeded += 1
                logger.info("update_all_prices: seeded entry price %s for %s #%d", price, symbol, rec_id)
        conn.commit()
        conn.close()
        if verbose and seeded:
            print(f"  🌱 Seeded entry prices for {seeded} new stock rec(s)")
    except Exception as exc:
        logger.warning("update_all_prices: entry price seeding failed: %s", exc)

    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT id, symbol, action, price_entry, created_at "
            "FROM recommendations "
            "WHERE price_entry IS NOT NULL AND price_entry > 0 "
            "ORDER BY created_at DESC"
        )
        recs = [dict(r) for r in cur.fetchall()]
        conn.close()
    except Exception as exc:
        logger.warning("update_all_prices: DB read failed: %s", exc)
        return {"scored": 0, "failed": 0, "skipped": 0}

    if not recs:
        if verbose:
            print("  📊 No scoreable recommendations found")
        return {"scored": 0, "failed": 0, "skipped": 0}

    if verbose:
        print(f"  🔄 Scoring {len(recs)} recommendations...")

    scored = failed = skipped = retry_total = 0

    for rec in recs:
        symbol      = rec["symbol"]
        action      = rec["action"]
        price_entry = float(rec["price_entry"])

        current = fetch_current_price(symbol)
        if current is None:
            if verbose:
                print(f"  ⚠️  [{rec['id']:>4}] {symbol}: price fetch failed")
            failed += 1
            continue

        current_f = float(current)
        current_return = round((current_f - price_entry) / price_entry * 100, 4)

        tx_cost = TRANSACTION_COSTS.get(action, 0.0)

        # Add prorated annual fee for funds based on days held
        ann_fee = ANNUAL_FEES.get(symbol, 0.0)
        if ann_fee > 0 and rec.get("created_at"):
            try:
                created = datetime.fromisoformat(rec["created_at"])
                days_held = max((datetime.utcnow() - created).days, 0)
                tx_cost += ann_fee * days_held / 365.0
            except (ValueError, TypeError):
                pass

        tx_cost = round(tx_cost, 4)
        net_return = round(current_return - tx_cost, 4)
        direction = score_direction(action, price_entry, current_f)
        now_str = datetime.utcnow().isoformat()

        try:
            conn = get_connection()
            with conn:
                conn.execute(
                    """UPDATE recommendations
                       SET current_price        = :cp,
                           current_return_pct   = :cr,
                           net_return_pct       = :nr,
                           direction_correct    = :dc,
                           transaction_cost_pct = :tc,
                           last_scored_at       = :ts
                       WHERE id = :id""",
                    {
                        "cp": round(current_f, 4),
                        "cr": current_return,
                        "nr": net_return,
                        "dc": direction,
                        "tc": tx_cost,
                        "ts": now_str,
                        "id": rec["id"],
                    },
                )
            conn.close()
            scored += 1

            if verbose:
                dir_sym = "✓" if direction == 1 else ("✗" if direction == 0 else "·")
                print(
                    f"  {dir_sym} [{rec['id']:>4}] {symbol:<20} "
                    f"₪{current_f:<10.4f} {current_return:+.2f}% net {net_return:+.2f}%"
                )
        except Exception as exc:
            logger.warning("update_all_prices: DB write failed for %s: %s", symbol, exc)
            failed += 1

    if verbose:
        print(f"\n  Scored: {scored} | Failed: {failed} | Skipped: {skipped}")

    # ── Retry phase: re-attempt failed price fetches after a short pause ───────
    # yfinance occasionally returns empty histories on the first call (rate limiting
    # or CDN propagation). A single 2s delay resolves most transient failures.
    if failed > 0:
        import time
        time.sleep(2)

        retry_count = retry_total = 0
        try:
            conn = get_connection()
            cur = conn.cursor()
            # Re-query only recs that still have no current_price
            cur.execute(
                "SELECT id, symbol, action, price_entry, created_at "
                "FROM recommendations "
                "WHERE price_entry IS NOT NULL AND price_entry > 0 "
                "AND (last_scored_at IS NULL OR current_price IS NULL) "
                "ORDER BY created_at DESC"
            )
            retry_recs = [dict(r) for r in cur.fetchall()]
            conn.close()
        except Exception as exc:
            logger.warning("update_all_prices retry: DB read failed: %s", exc)
            retry_recs = []

        for rec in retry_recs:
            time.sleep(0.2)   # gentle rate limiting
            symbol      = rec["symbol"]
            action      = rec["action"]
            price_entry = float(rec["price_entry"])

            current = fetch_current_price(symbol)
            if current is None:
                continue

            current_f      = float(current)
            current_return = round((current_f - price_entry) / price_entry * 100, 4)
            tx_cost        = TRANSACTION_COSTS.get(action, 0.0)
            ann_fee        = ANNUAL_FEES.get(symbol, 0.0)
            if ann_fee > 0 and rec.get("created_at"):
                try:
                    created   = datetime.fromisoformat(rec["created_at"])
                    days_held = max((datetime.utcnow() - created).days, 0)
                    tx_cost  += ann_fee * days_held / 365.0
                except (ValueError, TypeError):
                    pass
            tx_cost    = round(tx_cost, 4)
            net_return = round(current_return - tx_cost, 4)
            direction  = score_direction(action, price_entry, current_f)
            now_str    = datetime.utcnow().isoformat()

            try:
                conn = get_connection()
                with conn:
                    conn.execute(
                        """UPDATE recommendations
                           SET current_price        = :cp,
                               current_return_pct   = :cr,
                               net_return_pct       = :nr,
                               direction_correct    = :dc,
                               transaction_cost_pct = :tc,
                               last_scored_at       = :ts
                           WHERE id = :id""",
                        {
                            "cp": round(current_f, 4),
                            "cr": current_return,
                            "nr": net_return,
                            "dc": direction,
                            "tc": tx_cost,
                            "ts": now_str,
                            "id": rec["id"],
                        },
                    )
                conn.close()
                retry_count  += 1
                retry_total  += 1
                scored       += 1
                failed       -= 1
                if verbose:
                    dir_sym = "✓" if direction == 1 else ("✗" if direction == 0 else "·")
                    print(
                        f"  {dir_sym} [{rec['id']:>4}] {symbol:<20} "
                        f"₪{current_f:<10.4f} {current_return:+.2f}% (retry)"
                    )
            except Exception as exc:
                logger.warning("update_all_prices retry: DB write failed for %s: %s", symbol, exc)

        if verbose and retry_count:
            print(f"  ♻️  Retry: recovered {retry_count} ticker(s)")

    return {"scored": scored, "failed": failed, "skipped": skipped, "retry": retry_total}


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    print("\n" + "="*55)
    print("  💹  Price Updater — Open Positions")
    print("="*55 + "\n")
    summary = run_price_update(verbose=True)
    print(f"\n  Updated: {summary['updated']} | Failed: {summary['failed']}")
    print("="*55 + "\n")
