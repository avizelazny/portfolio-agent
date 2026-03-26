"""
Mutual Funds Connector - Israeli TTF/Kaspit funds
Sources (in priority order):
  1. funder.co.il  — inline fundData JSON (richest data)
  2. TASE services API — JSON endpoint (fallback when funder is down)
"""

import json
import logging
import re
from datetime import datetime

import requests

logger = logging.getLogger(__name__)

# User's 6 mutual funds
FUNDS = {
    5136544: "מיטב כספית שקלית כשרה",
    5130661: "הראל מחקה ת\"א 35",
    5109418: "תכלית TTF ת\"א 35",
    5134556: "תכלית TTF Indxx Semiconductor Equipment",
    5142088: "קסם KTF MarketVector תעשיות ביטחוניות ישראליות",
    5141882: "תכלית TTF אינדקס תעשיות ביטחוניות ישראל",
}

FUNDER_URL = "https://www.funder.co.il/fund/{fund_id}"
TASE_URL = "https://servicesm.tase.co.il/api/fund/{fund_id}/fundDetails"

FUNDER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
    "Accept-Language": "he-IL,he;q=0.9,en-US;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.funder.co.il/",
}

TASE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Origin": "https://www.tase.co.il",
    "Referer": "https://www.tase.co.il/",
}


def _extract_funder_data(html: str) -> dict | None:
    """Extract the inline fundData JSON object from a funder.co.il HTML page.

    Args:
        html: Raw HTML content of the fund page.

    Returns:
        The first entry from the fundData['x'] array, or None if not found.
    """
    match = re.search(r'fundData\s*=\s*(\{.*?"x"\s*:\s*\[.*?\].*?\})', html, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(1))
        return data["x"][0] if data.get("x") else None
    except Exception as e:
        logger.debug("funder JSON parse error: %s", e)
        try:
            arr_match = re.search(r'"x"\s*:\s*\[(\{[^]]+\})', html, re.DOTALL)
            if arr_match:
                return json.loads(arr_match.group(1))
        except Exception:
            pass
    return None


def _get_from_funder(fund_id: int) -> dict | None:
    """Fetch fund NAV and performance data from funder.co.il.

    Args:
        fund_id: Numeric TASE fund identifier.

    Returns:
        Dict of fund data fields, or None if the request or parse failed.
    """
    url = FUNDER_URL.format(fund_id=fund_id)
    try:
        resp = requests.get(url, headers=FUNDER_HEADERS, timeout=12)
        resp.encoding = "utf-8"
        if resp.status_code != 200:
            return None
        raw = _extract_funder_data(resp.text)
        if not raw:
            return None
        return {
            "fund_id": fund_id,
            "name": raw.get("fundName", FUNDS.get(fund_id, str(fund_id))),
            "nav": raw.get("sellPrice"),
            "buy_price": raw.get("buyPrice"),
            "change_1day": raw.get("1day"),
            "change_7day": raw.get("7days"),
            "change_30day": raw.get("30days"),
            "change_ytd": raw.get("yearBegin"),
            "change_1year": raw.get("1year"),
            "management_fee": raw.get("nemanut"),
            "nihol": raw.get("nihol"),
            "aum_millions": raw.get("rSize"),
            "manager": raw.get("fundMng"),
            "last_update": raw.get("lastUpdate"),
            "source": "funder.co.il",
        }
    except Exception as e:
        logger.debug("funder %s: %s", fund_id, e)
        return None


def _get_from_tase(fund_id: int) -> dict | None:
    """Fetch fund NAV data from the TASE services API (fallback source).

    Args:
        fund_id: Numeric TASE fund identifier.

    Returns:
        Dict of fund data fields, or None if the request or parse failed.
    """
    url = TASE_URL.format(fund_id=fund_id)
    try:
        resp = requests.get(url, headers=TASE_HEADERS, timeout=12)
        if resp.status_code != 200:
            return None
        data = resp.json()

        fd = data.get("FundDetails") or data.get("fundDetails") or data
        if isinstance(fd, list):
            fd = fd[0] if fd else {}

        nav = (
            fd.get("SellPrice") or fd.get("sellPrice") or
            fd.get("UnitPrice") or fd.get("unitPrice")
        )
        if not nav:
            return None

        last_update = (
            fd.get("LastUpdate") or fd.get("lastUpdate") or
            fd.get("PriceDate") or fd.get("priceDate")
        )
        return {
            "fund_id": fund_id,
            "name": fd.get("FundName") or fd.get("fundName") or FUNDS.get(fund_id, str(fund_id)),
            "nav": float(nav),
            "buy_price": fd.get("BuyPrice") or fd.get("buyPrice"),
            "change_1day": fd.get("DailyYield") or fd.get("dailyYield"),
            "change_7day": None,
            "change_30day": None,
            "change_ytd": fd.get("YearBeginYield") or fd.get("yearBeginYield"),
            "change_1year": fd.get("Yield1Year") or fd.get("yield1Year"),
            "management_fee": fd.get("ManagementFee") or fd.get("managementFee"),
            "nihol": None,
            "aum_millions": fd.get("FundSize") or fd.get("fundSize"),
            "manager": fd.get("FundMng") or fd.get("fundMng"),
            "last_update": str(last_update)[:10] if last_update else None,
            "source": "tase-api",
        }
    except Exception as e:
        logger.debug("TASE %s: %s", fund_id, e)
        return None


def get_fund_data(fund_id: int) -> dict | None:
    """Fetch fund data from funder.co.il, falling back to the TASE API.

    Args:
        fund_id: Numeric TASE fund identifier.

    Returns:
        Dict of fund data fields from the first successful source, or None.
    """
    data = _get_from_funder(fund_id)
    if data:
        return data
    logger.info("funder unavailable for %s, trying TASE API...", fund_id)
    data = _get_from_tase(fund_id)
    if data:
        return data
    logger.warning("Fund %s: all sources failed", fund_id)
    return None


def get_all_funds() -> dict[int, dict | None]:
    """Fetch data for all configured funds in FUNDS.

    Returns:
        Dict mapping fund_id to data dict (or None if all sources failed).
    """
    results = {}
    for fund_id, name in FUNDS.items():
        logger.info("Fetching fund %s (%s)...", fund_id, name)
        data = get_fund_data(fund_id)
        results[fund_id] = data
        if data:
            logger.info(
                "  ✓ [%s] NAV=%s, YTD=%s%%, 1Y=%s%%, fee=%s%%",
                data["source"],
                data["nav"],
                data.get("change_ytd"),
                data.get("change_1year"),
                data.get("management_fee"),
            )
        else:
            logger.warning("  ✗ Failed to fetch fund %s", fund_id)
    return results


def format_funds_for_agent(funds_data: dict) -> str:
    """Format all fund data as a Hebrew-language text block for the agent prompt.

    Args:
        funds_data: Dict mapping fund_id to data dict as returned by get_all_funds().

    Returns:
        Multi-line Hebrew string with NAV, performance, and fee data per fund.
    """
    lines = ["=== קרנות נאמנות / קרנות כספיות ==="]
    lines.append(f"עדכון: {datetime.now().strftime('%d/%m/%Y %H:%M')}\n")

    ok = {k: v for k, v in funds_data.items() if v}
    failed = [k for k, v in funds_data.items() if not v]

    for fund_id, d in ok.items():
        lines.append(f"קרן {fund_id}: {d['name']}")
        if d.get("nav"):
            lines.append(f"  NAV (מחיר פדיון): \u20aa{d['nav']:,.2f}")
        if d.get("change_1day") is not None:
            lines.append(f"  שינוי יומי: {d['change_1day']:+.2f}%")
        if d.get("change_ytd") is not None:
            lines.append(f"  מתחילת השנה: {d['change_ytd']:+.2f}%")
        if d.get("change_1year") is not None:
            lines.append(f"  תשואה 12 חודש: {d['change_1year']:+.2f}%")
        if d.get("management_fee") is not None:
            lines.append(f"  דמי ניהול: {d['management_fee']:.2f}%")
        if d.get("aum_millions"):
            lines.append(f"  שווי קרן: \u20aa{d['aum_millions']:,.1f}M")
        if d.get("last_update"):
            lines.append(f"  עדכון אחרון: {d['last_update'][:10]}")
        lines.append("")

    if failed:
        lines.append(f"לא נמצא נתונים עבור: {', '.join(str(f) for f in failed)}")

    return "\n".join(lines)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    print("מושך נתוני קרנות נאמנות...\n")
    funds = get_all_funds()
    print("\n" + format_funds_for_agent(funds))
