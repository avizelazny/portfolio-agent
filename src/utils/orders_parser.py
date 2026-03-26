"""Parser for Bank Discount open orders export (הוראות וביצועים — טאב הוראות).

File format (xlsx):
  - Header row contains 'שם נייר' in column A
  - Col A: שם נייר (security name)
  - Col B: סטטוס (status — 'ממתין' = pending)
  - Col C: סוג פעולה (action — מכירה=SELL, קניה=BUY)
  - Col D: כמות מבוקשת/מבוצעת (qty — format "executed / requested")
  - Col E: הגבלת שער (limit price — in agorot for funds)
  - Col J: תאריך מתן הוראה (order placement date)
"""

import pandas as pd
from typing import Optional


# Map Hebrew security names (as they appear in the export) to TASE security IDs.
# Add new entries as new securities appear in orders.
NAME_TO_ID: dict[str, str] = {
    "תכלית סל אינדקס תעשיות ביטחוניות ישראל": "1235985",
    "הראל מחקה ת\"א 35": "5130661",
    "מיטב כספית שקלית כשרה": "5136544",
    "קסם KTF MarketVector תעשיות בטחוניות ישראליות": "5142088",
    "תכלית TTF אינדקס תעשיות ביטחוניות ישראל": "5141882",
    "תכלית TTF Semiconductor": "5134556",
}

ACTION_MAP: dict[str, str] = {
    "מכירה": "SELL",
    "קניה": "BUY",
}


def parse_open_orders(filepath: str) -> list[dict]:
    """Parse Bank Discount open orders xlsx file.

    Reads the הוראות וביצועים export, filters to rows where status contains
    'ממתין' (pending), and returns them as dicts compatible with the
    pending_orders format used in portfolio.yaml.

    Limit prices stored in agorot (> 1000) are automatically divided by 100
    to convert to shekels, consistent with how fund prices are handled
    throughout the rest of the project.

    Args:
        filepath: Path to the הוראות וביצועים xlsx file (טאב הוראות sheet).

    Returns:
        List of pending order dicts with keys: security_id, name, action,
        quantity, limit_price, placed_date.

    Raises:
        ValueError: If the header row containing 'שם נייר' cannot be found.
    """
    df = pd.read_excel(filepath, header=None)

    # Find header row — the row where column A contains 'שם נייר'
    header_row: Optional[int] = None
    for i, row in df.iterrows():
        if str(row.iloc[0]).strip() == "שם נייר":
            header_row = i
            break

    if header_row is None:
        raise ValueError(
            "Could not find header row ('שם נייר') in open orders file. "
            "Make sure you exported from הוראות וביצועים → טאב הוראות."
        )

    df.columns = df.iloc[header_row]
    df = df.iloc[header_row + 1:].reset_index(drop=True)

    orders: list[dict] = []
    for _, row in df.iterrows():
        status = str(row.get("סטטוס", "")).strip()
        if "ממתין" not in status:
            continue  # skip executed, cancelled, or empty rows

        name = str(row.get("שם נייר", "")).strip()
        if not name or name == "nan":
            continue

        action_heb = str(row.get("סוג פעולה", "")).strip()
        action = ACTION_MAP.get(action_heb, action_heb)

        # Quantity column shows "executed / requested" — take the requested (last) part
        qty_raw = str(row.get("כמות מבוקשת/ מבוצעת", "")).strip()
        try:
            quantity = int(qty_raw.split("/")[-1].strip())
        except (ValueError, AttributeError):
            quantity = 0

        # Limit price — funds use agorot (> 1000), divide by 100 to get shekels
        limit_raw = row.get("הגבלת שער")
        try:
            limit_price = float(limit_raw)
            if limit_price > 1000:
                limit_price = round(limit_price / 100, 2)
        except (TypeError, ValueError):
            limit_price = 0.0

        # Order date
        date_raw = row.get("תאריך מתן הוראה", "")
        try:
            placed_date = str(pd.to_datetime(date_raw).date())
        except Exception:
            placed_date = ""

        security_id = NAME_TO_ID.get(name, "")

        orders.append({
            "security_id": security_id,
            "name": name,
            "action": action,
            "quantity": quantity,
            "limit_price": limit_price,
            "placed_date": placed_date,
        })

    return orders


def format_orders_for_prompt(orders: list[dict]) -> str:
    """Format pending orders as a context string for Claude's prompt.

    Args:
        orders: List of pending order dicts as returned by parse_open_orders().

    Returns:
        Multi-line string describing the open orders, or empty string if none.
    """
    if not orders:
        return ""
    lines = [
        f"  - {o['action']} {o['name']} ({o['security_id']}): "
        f"{o['quantity']} units @ \u20aa{o['limit_price']} limit (placed {o['placed_date']})"
        for o in orders
    ]
    total_units = sum(o["quantity"] for o in orders)
    lines.append(f"  Total: {len(orders)} pending orders, {total_units} units")
    return "\n".join(lines)
