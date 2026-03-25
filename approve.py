import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

"""
approve.py
====================================
CLI tool for managing recommendations — approve, reject, close positions.

Usage:
    python approve.py                    # Interactive mode — shows pending list
    python approve.py pending            # Show all pending recommendations
    python approve.py open               # Show open approved positions
    python approve.py yes 42             # Approve rec #42 (will ask for price)
    python approve.py yes 42 38.50       # Approve rec #42, bought at ₪38.50
    python approve.py yes 42 38.50 100   # Approve + 100 units
    python approve.py no  42             # Reject rec #42
    python approve.py close 42           # Close position #42 (fetch price auto)
    python approve.py close 42 40.20     # Close position #42 at ₪40.20
    python approve.py perf               # Show performance summary
    python approve.py perf 60            # Performance over last 60 days
    python approve.py orders list        # Show pending limit orders
    python approve.py orders add         # Add a new limit order interactively
    python approve.py orders remove <n>  # Remove order by number
    python approve.py orders clear       # Remove all pending orders
    python approve.py manual             # Log an off-system trade with no matching rec
"""

import sys
import os
from datetime import datetime
from decimal import Decimal, InvalidOperation
from dotenv import load_dotenv

load_dotenv()

import logging
logging.getLogger("src.db.recommendations_db").setLevel(logging.CRITICAL)
logging.getLogger("src.price_updater").setLevel(logging.CRITICAL)


# -- Formatting helpers --------------------------------------------------------

def fmt_price(p) -> str:
    if p is None: return "—"
    return f"₪{float(p):,.4f}"

def fmt_pct(p) -> str:
    if p is None: return "—"
    return f"{float(p):+.2f}%"

def fmt_date(d) -> str:
    if d is None: return "—"
    if hasattr(d, "strftime"):
        return d.strftime("%Y-%m-%d %H:%M")
    return str(d)

CONVICTION_ICONS = {"HIGH": "[H]", "MEDIUM": "[M]", "LOW": "[L]"}
ACTION_ICONS = {"BUY": "[BUY]", "SELL": "[SEL]", "HOLD": "[->]", "WATCH": "[W]"}


# -- Display functions ---------------------------------------------------------

def show_pending(recs):
    if not recs:
        print("\n  [OK] No pending recommendations.\n")
        return

    print(f"\n{'-'*80}")
    print(f"  PENDING RECOMMENDATIONS ({len(recs)} awaiting your decision)")
    print(f"{'-'*80}")

    for r in recs:
        conv  = CONVICTION_ICONS.get(r["conviction"], "")
        act   = ACTION_ICONS.get(r["action"], "")
        print(f"\n  [{r['id']:>4}] {act} {r['action']:<5} {conv} {r['conviction']:<8} "
              f"{r['symbol']:<22} {fmt_date(r['created_at'])}")
        print(f"         Entry: {fmt_price(r['price_entry'])}  Target: {fmt_price(r['price_target'])}")
        print(f"         Thesis: {str(r['thesis'])[:100]}...")
        print(f"         Risk:   {str(r['key_risk'])[:100]}...")

    print(f"\n{'-'*80}")
    print("  Commands:")
    print("    python approve.py yes <id> [price] [qty]   — approve")
    print("    python approve.py no  <id>                 — reject")
    print(f"{'-'*80}\n")


def show_open(recs):
    if not recs:
        print("\n   No open approved positions.\n")
        return

    print(f"\n{'-'*80}")
    print(f"  OPEN POSITIONS ({len(recs)})")
    print(f"{'-'*80}")
    print(f"  {'ID':>4}  {'Symbol':<20} {'Action':<6} {'Entry':>10} "
          f"{'Current':>10} {'P&L':>8}  {'Days':>4}  {'Target':>10}")
    print(f"  {'-'*4}  {'-'*20} {'-'*6} {'-'*10} {'-'*10} {'-'*8}  {'-'*4}  {'-'*10}")

    for r in recs:
        entry   = r.get("actual_price") or r.get("price_entry")
        current = r.get("price_current")
        target  = r.get("price_target")

        if entry and current and float(entry) > 0:
            pnl = (float(current) - float(entry)) / float(entry) * 100
            pnl_str = f"{pnl:+.2f}%"
            arrow = "[BUY]" if pnl >= 0 else "[SEL]"
        else:
            pnl_str = "—"
            arrow = "  "

        days = ""
        if r.get("created_at"):
            d = r["created_at"]
            if isinstance(d, str):
                try:
                    d = datetime.strptime(d[:19], "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    d = None
            if d:
                days = str((datetime.now() - d.replace(tzinfo=None)).days) + "d"

        target_str = fmt_price(target) if target else "—"
        print(f"  {r['id']:>4}  {r['symbol']:<20} {r['action']:<6} "
              f"{fmt_price(entry):>10} {fmt_price(current):>10} "
              f"{pnl_str:>8}  {days:>4}  {target_str:>10}  {arrow}")

    print(f"\n{'-'*80}")
    print("  Commands:")
    print("    python approve.py close <id> [exit_price]  — close position")
    print(f"{'-'*80}\n")


def show_performance(summary):
    if not summary:
        print("\n  [~] No performance data yet — approve some recommendations first.\n")
        return

    print(f"\n{'═'*60}")
    print(f"  AGENT PERFORMANCE — last {summary.period_days} days")
    print(f"{'═'*60}")
    print(f"  Recommendations:  {summary.total_recs} made  |  "
          f"{summary.approved_recs} approved  |  {summary.successful_recs} successful")
    print(f"  Win rate:         {summary.success_rate}%")
    print()
    print(f"  Avg return:       {summary.avg_return_pct:+.2f}%")
    print(f"  Avg benchmark:    {summary.avg_benchmark_pct:+.2f}%  (TA-35)")
    print(f"  Avg alpha:        {summary.avg_alpha:+.2f}%")
    print()
    if summary.open_positions:
        print(f"  Open positions:   {summary.open_positions}  |  "
              f"unrealized {summary.open_unrealized_pct:+.2f}%")
    if summary.best_trade:
        print(f"  Best trade:       {summary.best_trade}")
    if summary.worst_trade:
        print(f"  Worst trade:      {summary.worst_trade}")
    print()
    print(f"  Conviction accuracy:")
    print(f"    HIGH:   {summary.high_conv_success}%")
    print(f"    MEDIUM: {summary.med_conv_success}%")
    print(f"    LOW:    {summary.low_conv_success}%")
    if summary.top_pattern:
        print(f"  Best pattern:     {summary.top_pattern}")
    print(f"{'═'*60}\n")


# -- Action handlers -----------------------------------------------------------

def do_approve(rec_id: int, price_str: str = None, qty_str: str = None):
    from src.db.recommendations_db import update_approval, get_pending_recs
    from src.models.recommendation import ApprovalUpdate

    actual_price = None
    if price_str:
        try:
            actual_price = Decimal(price_str.replace("₪","").replace(",",""))
        except InvalidOperation:
            print(f"  [NO] Invalid price: {price_str}")
            return

    quantity = None
    if qty_str:
        try:
            quantity = int(qty_str)
        except ValueError:
            print(f"  [NO] Invalid quantity: {qty_str}")
            return

    # Interactive: ask for price if not provided (skip if stdin is not a tty)
    if actual_price is None:
        try:
            val = input(f"  Actual trade price for #{rec_id} (Enter to skip): ").strip()
        except EOFError:
            val = ""
        if val:
            try:
                actual_price = Decimal(val.replace("₪","").replace(",",""))
            except InvalidOperation:
                print("  [NO] Invalid price — approval cancelled")
                return

    if quantity is None and actual_price is not None:
        try:
            val = input(f"  Quantity (Enter to skip): ").strip()
        except EOFError:
            val = ""
        if val:
            try:
                quantity = int(val)
            except ValueError:
                pass

    try:
        note = input(f"  Note (optional, Enter to skip): ").strip()
    except EOFError:
        note = ""

    update = ApprovalUpdate(
        rec_id       = rec_id,
        approved     = True,
        actual_price = actual_price,
        quantity     = quantity,
        note         = note
    )
    from src.db.recommendations_db import update_approval
    update_approval(update)
    print(f"\n  [OK] Recommendation #{rec_id} APPROVED")
    if actual_price:
        print(f"     Traded at: {fmt_price(actual_price)}", end="")
        if quantity:
            print(f"  ×  {quantity:,} units", end="")
        print()
    print()


def do_reject(rec_id: int):
    from src.models.recommendation import ApprovalUpdate
    from src.db.recommendations_db import update_approval

    try:
        note = input(f"  Reason for rejecting #{rec_id} (optional): ").strip()
    except EOFError:
        note = ""
    update = ApprovalUpdate(rec_id=rec_id, approved=False, note=note)
    update_approval(update)
    print(f"\n  [NO] Recommendation #{rec_id} REJECTED\n")


def do_close(rec_id: int, price_str: str = None):
    from src.db.recommendations_db import close_position, get_open_approved_recs
    from src.price_updater import fetch_price_yahoo, fetch_ta35

    # Find the rec to show context
    open_recs = get_open_approved_recs()
    rec = next((r for r in open_recs if r["id"] == rec_id), None)
    if not rec:
        print(f"\n  [NO] No open approved position found with id #{rec_id}\n")
        return

    # Get exit price
    if price_str:
        try:
            exit_price = Decimal(price_str.replace("₪","").replace(",",""))
        except InvalidOperation:
            print(f"  [NO] Invalid price: {price_str}")
            return
    else:
        print(f"\n  Fetching current price for {rec['symbol']}...")
        fetched = fetch_price_yahoo(rec["symbol"])
        if fetched:
            print(f"  Current price: {fmt_price(fetched)}")
            confirm = input("  Use this as exit price? [Y/n]: ").strip().lower()
            if confirm in ("n", "no"):
                val = input("  Enter exit price: ").strip()
                try:
                    exit_price = Decimal(val.replace("₪","").replace(",",""))
                except InvalidOperation:
                    print("  [NO] Invalid price — close cancelled")
                    return
            else:
                exit_price = fetched
        else:
            val = input("  Could not fetch price. Enter exit price manually: ").strip()
            try:
                exit_price = Decimal(val.replace("₪","").replace(",",""))
            except InvalidOperation:
                print("  [NO] Invalid price — close cancelled")
                return

    # Fetch TA-35 for benchmark
    print("  Fetching TA-35 level for benchmark...")
    ta35 = fetch_ta35()

    close_position(rec_id, exit_price, ta35)

    entry = rec.get("actual_price") or rec.get("price_entry")
    if entry and float(entry) > 0:
        ret_pct = (float(exit_price) - float(entry)) / float(entry) * 100
        print(f"\n  [OK] Position #{rec_id} ({rec['symbol']}) CLOSED")
        print(f"     Entry:  {fmt_price(entry)}")
        print(f"     Exit:   {fmt_price(exit_price)}")
        print(f"     Return: {ret_pct:+.2f}%")
        if ta35 and rec.get("ta35_at_entry"):
            bench = (float(ta35) - float(rec["ta35_at_entry"])) / float(rec["ta35_at_entry"]) * 100
            alpha = ret_pct - bench
            print(f"     Benchmark (TA-35): {bench:+.2f}%")
            print(f"     Alpha:             {alpha:+.2f}%")
    else:
        print(f"\n  [OK] Position #{rec_id} closed at {fmt_price(exit_price)}")
    print()


# -- Interactive mode ----------------------------------------------------------

def interactive_mode():
    from src.db.recommendations_db import get_pending_recs, get_open_approved_recs

    print("\n" + "="*60)
    print("  [AI]  Portfolio Agent — Approval Console")
    print("="*60)

    pending = get_pending_recs()
    open_recs = get_open_approved_recs()

    print(f"\n  Pending:        {len(pending)} recommendations")
    print(f"  Open positions: {len(open_recs)}")
    print()
    print("  Options:")
    print("    [1] Review pending recommendations")
    print("    [2] View open positions")
    print("    [3] Performance summary")
    print("    [4] Exit")
    print()

    choice = input("  Select [1-4]: ").strip()

    if choice == "1":
        show_pending(pending)
        if pending:
            print("  Enter approval commands (or press Enter to exit):")
            while True:
                cmd = input("  > ").strip()
                if not cmd:
                    break
                parts = cmd.split()
                if len(parts) >= 2:
                    action = parts[0].lower()
                    try:
                        rec_id = int(parts[1])
                    except ValueError:
                        print("  Invalid id")
                        continue
                    if action in ("yes", "y", "approve"):
                        do_approve(rec_id, parts[2] if len(parts)>2 else None,
                                           parts[3] if len(parts)>3 else None)
                    elif action in ("no", "n", "reject"):
                        do_reject(rec_id)
                    else:
                        print("  Unknown command")

    elif choice == "2":
        show_open(open_recs)
        if open_recs:
            cmd = input("  Close a position? Enter: close <id> [price] (or Enter to skip): ").strip()
            if cmd.startswith("close"):
                parts = cmd.split()
                if len(parts) >= 2:
                    try:
                        do_close(int(parts[1]), parts[2] if len(parts) > 2 else None)
                    except ValueError:
                        print("  Invalid id")

    elif choice == "3":
        from src.db.recommendations_db import get_performance_summary
        days_str = input("  Days to look back [30]: ").strip()
        days = int(days_str) if days_str.isdigit() else 30
        summary = get_performance_summary(days)
        show_performance(summary)

    elif choice == "4":
        print()
        return
    else:
        print("  Invalid choice\n")


# -- DB sanity check -----------------------------------------------------------

def _check_db() -> None:
    """Verify the DB file exists and has the recommendations table.

    Prints the resolved DB path on every invocation so the user can confirm
    which file is being used. Exits with a clear error if the file is missing
    or the table has not been created yet.
    """
    import sqlite3
    from src.db.recommendations_db import _DB_PATH

    print(f"  [DB] {_DB_PATH}")

    if not _DB_PATH.exists():
        print(f"\n  [ERROR] Database not found: {_DB_PATH}")
        print("  Run the agent at least once to create it (or check the path).")
        sys.exit(1)

    conn = sqlite3.connect(str(_DB_PATH))
    cur = conn.cursor()
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='recommendations'"
    )
    has_table = cur.fetchone() is not None
    conn.close()

    if not has_table:
        print(f"\n  [ERROR] No 'recommendations' table in {_DB_PATH}")
        print("  The DB file may be empty or point to the wrong location.")
        sys.exit(1)


# -- Entry point ---------------------------------------------------------------

def main():
    _check_db()
    args = sys.argv[1:]

    if not args:
        interactive_mode()
        return

    cmd = args[0].lower()

    if cmd == "pending":
        from src.db.recommendations_db import get_pending_recs
        show_pending(get_pending_recs())

    elif cmd == "open":
        from src.db.recommendations_db import get_open_approved_recs
        show_open(get_open_approved_recs())

    elif cmd in ("yes", "y", "approve"):
        if len(args) < 2:
            print("  Usage: python approve.py yes <id> [price] [qty]")
            sys.exit(1)
        do_approve(int(args[1]),
                   args[2] if len(args) > 2 else None,
                   args[3] if len(args) > 3 else None)

    elif cmd in ("no", "n", "reject"):
        if len(args) < 2:
            print("  Usage: python approve.py no <id>")
            sys.exit(1)
        do_reject(int(args[1]))

    elif cmd == "close":
        if len(args) < 2:
            print("  Usage: python approve.py close <id> [exit_price]")
            sys.exit(1)
        do_close(int(args[1]), args[2] if len(args) > 2 else None)

    elif cmd in ("perf", "performance"):
        from src.db.recommendations_db import get_performance_summary
        days = int(args[1]) if len(args) > 1 and args[1].isdigit() else 30
        summary = get_performance_summary(days)
        show_performance(summary)

    elif cmd == "supersede":
        date_prefix = args[1] if len(args) > 1 else None
        if not date_prefix:
            print("  Usage: python approve.py supersede YYYY-MM-DD")
            sys.exit(1)
        from src.db.recommendations_db import get_connection
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """UPDATE recommendations
                   SET approved=0, approval_note='superseded by newer batch'
                   WHERE approved IS NULL AND created_at LIKE ?""",
                (f"{date_prefix}%",),
            )
            count = cur.rowcount
            conn.commit()
        print(f"  Rejected {count} pending recommendations from {date_prefix}")

    elif cmd == "supersede-all":
        from src.db.recommendations_db import get_connection
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT MAX(created_at) FROM recommendations WHERE approved IS NULL")
            row = cur.fetchone()
            if not row or not row[0]:
                print("  No pending recommendations found.")
            else:
                latest = row[0][:16]  # YYYY-MM-DD HH:MM
                cur.execute(
                    """UPDATE recommendations
                       SET approved=0, approval_note='superseded by newer batch'
                       WHERE approved IS NULL AND created_at NOT LIKE ?""",
                    (f"{latest}%",),
                )
                count = cur.rowcount
                conn.commit()
                print(f"  Rejected {count} pending recommendations — kept latest batch ({latest})")

    elif cmd == "orders":
        subcommand = args[1] if len(args) > 1 else "list"
        import yaml

        def load_yaml():
            with open("portfolio.yaml", encoding="utf-8") as f:
                return yaml.safe_load(f)

        def save_yaml(data):
            with open("portfolio.yaml", "w", encoding="utf-8") as f:
                yaml.dump(data, f, allow_unicode=True, default_flow_style=False)

        if subcommand == "list":
            data = load_yaml()
            orders = data.get("pending_orders", [])
            if not orders:
                print("No pending limit orders in portfolio.yaml")
            else:
                print(f"\nPending limit orders ({len(orders)}):")
                print("─" * 50)
                for i, o in enumerate(orders, 1):
                    print(f"  {i}. {o['name']} ({o['security_id']})")
                    print(f"     {o['action']} {o['quantity']} units @ ₪{o['limit_price']}")
                    print(f"     Placed: {o['placed_date']}")
                print()

        elif subcommand == "add":
            data = load_yaml()
            if "pending_orders" not in data:
                data["pending_orders"] = []
            print("\nAdd new pending limit order:")
            security_id = input("Security ID (e.g. 1235985): ").strip()
            name = input("Security name (Hebrew ok): ").strip()
            action = input("Action (BUY/SELL): ").strip().upper()
            quantity = int(input("Quantity (units): ").strip())
            limit_price = float(input("Limit price (₪): ").strip())
            placed_date = input("Placed date (YYYY-MM-DD) [today]: ").strip()
            if not placed_date:
                from datetime import date
                placed_date = str(date.today())
            order = {
                "security_id": security_id,
                "name": name,
                "action": action,
                "quantity": quantity,
                "limit_price": limit_price,
                "placed_date": placed_date,
            }
            data["pending_orders"].append(order)
            save_yaml(data)
            print(f"\n[OK] Added: {action} {quantity} units of {name} @ ₪{limit_price}")

        elif subcommand == "remove":
            n = int(args[2]) - 1 if len(args) > 2 else None
            data = load_yaml()
            orders = data.get("pending_orders", [])
            if n is None or n < 0 or n >= len(orders):
                print("Usage: approve.py orders remove [number from list]")
            else:
                removed = orders.pop(n)
                data["pending_orders"] = orders
                save_yaml(data)
                print(f"[OK] Removed: {removed['action']} {removed['name']} @ ₪{removed['limit_price']}")

        elif subcommand == "clear":
            confirm = input("Clear ALL pending orders? (yes/no): ").strip().lower()
            if confirm == "yes":
                data = load_yaml()
                data["pending_orders"] = []
                save_yaml(data)
                print("[OK] All pending orders cleared from portfolio.yaml")
            else:
                print("Cancelled.")

        else:
            print("Usage: approve.py orders [list|add|remove <n>|clear]")

    elif cmd == "manual":
        """Log a manually executed trade with no matching recommendation.

        Creates a new approved recommendation record with execution data.
        Use this for off-system trades or when auto-matcher can't find a match.
        The record will be picked up by the quality tracker and snapshot runner.
        """
        from datetime import date
        from src.db.recommendations_db import get_connection, init_recommendations_table

        print("\n  Manual trade log — creates a new approved recommendation record")
        print("  Use this for off-system trades or when auto-matcher finds no match")
        print("  " + "─" * 55)

        symbol     = input("  Security ID or ticker (e.g. 1235985 or ESLT): ").strip()
        action     = input("  Action (BUY/SELL/TRIM): ").strip().upper()
        conviction = input("  Conviction (HIGH/MEDIUM/LOW) [MEDIUM]: ").strip().upper() or "MEDIUM"
        actual_price = float(input("  Actual execution price (\u20aa): ").strip())
        actual_qty   = int(input("  Quantity (units): ").strip())

        date_input  = input(f"  Execution date (YYYY-MM-DD) [today {date.today()}]: ").strip()
        executed_at = date_input if date_input else str(date.today())

        thesis   = input("  Reason / thesis (optional): ").strip() \
                   or f"Manual trade \u2014 {action} {symbol} @ \u20aa{actual_price}"
        key_risk = input("  Key risk (optional): ").strip() \
                   or "N/A \u2014 manually logged trade"

        init_recommendations_table()
        conn = get_connection()
        cur  = conn.cursor()
        cur.execute("""
            INSERT INTO recommendations
                (symbol, action, conviction, thesis, key_risk,
                 price_entry, price_actual, qty_actual, executed_at,
                 run_type, approved, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'manual', 1, datetime('now'))
        """, (
            symbol, action, conviction, thesis, key_risk,
            actual_price,   # price_entry = actual price (no prior recommendation)
            actual_price,   # price_actual = same
            actual_qty,
            executed_at,
        ))
        conn.commit()
        rec_id = cur.lastrowid
        conn.close()

        print(f"\n  [OK] Manual trade logged:")
        print(f"       Rec ID:   #{rec_id}")
        print(f"       Action:   {action} {symbol}")
        print(f"       Price:    \u20aa{actual_price:,.2f}")
        print(f"       Quantity: {actual_qty:,} units")
        print(f"       Date:     {executed_at}")
        print(f"       Status:   approved")
        print(f"\n       This trade will be scored by the quality tracker automatically.")
        print(f"       7/30/90 day snapshots will start from today's price.\n")

    elif cmd == "reset":
        from src.db.recommendations_db import get_connection
        print("  [!] This will permanently delete ALL recommendations from the DB.")
        print("      Your trade history is safe in Bank Discount ביצועים היסטוריים.")
        confirm = input("  Type 'yes' to confirm: ").strip().lower()
        if confirm != "yes":
            print("  Cancelled.")
            sys.exit(0)
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM recommendations")
        deleted = cur.rowcount
        cur.execute("DELETE FROM recommendation_snapshots")
        snapshots = cur.rowcount
        cur.execute("DELETE FROM sqlite_sequence WHERE name='recommendations'")
        cur.execute("DELETE FROM sqlite_sequence WHERE name='recommendation_snapshots'")
        conn.commit()
        conn.close()
        print(f"\n  [OK] DB reset complete — {deleted} recommendations + {snapshots} snapshots deleted.")
        print(f"       IDs will start from 1 on next run.")
        print(f"       Ready for first production batch.\n")

    else:
        print(__doc__)


if __name__ == "__main__":
    main()
