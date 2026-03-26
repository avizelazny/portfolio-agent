"""Funder.co.il connector — end-of-day NAV for Israeli mutual funds.

Extracts the NAV (net asset value) for a mutual fund from the funder.co.il
fund page. The NAV is embedded in an inline JavaScript variable:

    var fundData = {"x": [{"buyPrice": 106.02, "sellPrice": 106.02, ...}]}

CRITICAL: Values on funder.co.il are in AGOROT (hundredths of a shekel).
Always divide by 100 to convert to shekels before use.

URL pattern:
    https://www.funder.co.il/fund/{security_id}
"""
import json
import logging
import re
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "he-IL,he;q=0.9,en-US;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.funder.co.il/",
}

_BASE_URL = "https://www.funder.co.il/fund/{security_id}"


def fetch_nav(security_id: str) -> Optional[float]:
    """Fetch the latest NAV for a mutual fund from funder.co.il.

    Parses the inline ``fundData`` JavaScript variable from the fund page
    and returns the ``sellPrice`` field converted from agorot to shekels.

    Args:
        security_id: TASE numeric fund identifier (e.g. "5142088").

    Returns:
        NAV in ILS (shekels) as a float, or None if the fetch or parse fails.
    """
    url = _BASE_URL.format(security_id=security_id)
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=12)
        resp.encoding = "utf-8"

        if resp.status_code != 200:
            logger.warning(
                "funder_connector: HTTP %s fetching fund %s", resp.status_code, security_id
            )
            print(f"  ⚠ {security_id}: price unavailable (HTTP {resp.status_code})")
            return None

        price_agorot = _extract_nav_agorot(resp.text)

        if price_agorot is None:
            logger.warning(
                "funder_connector: could not extract NAV for fund %s", security_id
            )
            print(f"  ⚠ {security_id}: price unavailable (parse failed)")
            return None

        price_ils = price_agorot / 100.0
        print(f"  ✓ {security_id}: ₪{price_ils:.4f}  ({price_agorot} agorot)")
        return price_ils

    except requests.RequestException as e:
        logger.warning("funder_connector: network error for fund %s: %s", security_id, e)
        print(f"  ⚠ {security_id}: price unavailable (network error)")
        return None


def _extract_nav_agorot(html: str) -> Optional[float]:
    """Extract the raw NAV value (in agorot) from the fundData JS variable.

    Looks for the pattern ``var fundData = {"x": [{...}]}`` and extracts
    the ``sellPrice`` field. Falls back to ``buyPrice`` if ``sellPrice``
    is absent. Both fields are in agorot on funder.co.il.

    Args:
        html: Raw HTML of the funder.co.il fund page.

    Returns:
        Raw NAV value in agorot as float, or None if not found.
    """
    # Primary: parse the full fundData JSON object
    match = re.search(
        r'fundData\s*=\s*(\{.*?"x"\s*:\s*\[.*?\].*?\})', html, re.DOTALL
    )
    if match:
        try:
            data = json.loads(match.group(1))
            entry = data.get("x", [None])[0]
            if entry:
                price = entry.get("sellPrice") or entry.get("buyPrice")
                if price is not None:
                    return float(price)
        except (json.JSONDecodeError, IndexError, TypeError):
            pass

    # Fallback: regex directly on the price fields
    for field in ("sellPrice", "buyPrice"):
        m = re.search(rf'"{field}"\s*:\s*([\d]+\.?\d*)', html)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                continue

    return None
