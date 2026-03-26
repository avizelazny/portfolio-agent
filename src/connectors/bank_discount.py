"""
Bank Discount Open Banking Connector
=====================================
Implements the Bank Discount PSD2/Open Banking API.
Currently runs in MOCK mode — swap MOCK_MODE=False and provide real
credentials to connect to your live account.

Real API docs: https://developer.discountbank.co.il/openapi/
Auth flow: OAuth2 with mTLS (client certificate)
"""

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
MOCK_MODE = os.getenv("BANK_DISCOUNT_MOCK", "true").lower() == "true"
BASE_URL = os.getenv("BANK_DISCOUNT_BASE_URL", "https://api.discountbank.co.il/openbanking/v1")
CLIENT_ID = os.getenv("BANK_DISCOUNT_CLIENT_ID", "")
CERT_PATH = os.getenv("BANK_DISCOUNT_CERT_PATH", "portfolio-agent-app.crt")
KEY_PATH = os.getenv("BANK_DISCOUNT_KEY_PATH", "portfolio-agent-app.key")


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class Position:
    """A single security position held in a brokerage account."""

    symbol: str
    name: str
    quantity: float
    avg_cost: float  # NIS per unit
    current_price: float  # NIS per unit
    market_value: float  # NIS
    unrealized_pnl: float  # NIS
    pnl_pct: float  # %
    asset_type: str  # STOCK / BOND / ETF


@dataclass
class Portfolio:
    """Full portfolio snapshot for a single brokerage account."""

    account_id: str
    account_name: str
    total_value: float  # NIS
    cash_balance: float  # NIS
    invested_value: float  # NIS
    total_pnl: float  # NIS
    total_pnl_pct: float  # %
    positions: list[Position] = field(default_factory=list)
    as_of: str = ""


# ── Mock data ─────────────────────────────────────────────────────────────────

def _mock_portfolio() -> Portfolio:
    """Realistic mock of an Israeli retail investor portfolio."""
    positions = [
        Position("TEVA",  "Teva Pharmaceutical",      500,  38.20,  41.50,  20750.0,  1650.0,  4.32,  "STOCK"),
        Position("NICE",  "NICE Systems",              50,  410.00, 435.00,  21750.0,  1250.0,  3.05,  "STOCK"),
        Position("CHKP",  "Check Point Software",      30,  520.00, 545.00,  16350.0,   750.0,  4.81,  "STOCK"),
        Position("LUMI",  "Bank Leumi",               800,   22.50,  24.10,  19280.0,  1280.0,  7.11,  "STOCK"),
        Position("ICL",   "ICL Group",               1000,   16.80,  17.20,  17200.0,   400.0,  2.38,  "STOCK"),
        Position("ESLT",  "Elbit Systems",             25,  580.00, 612.00,  15300.0,   800.0,  5.52,  "STOCK"),
        Position("MGDL",  "Migdal Insurance",         600,   10.20,  10.85,   6510.0,   390.0,  6.38,  "STOCK"),
        Position("AMOT",  "Amot Investments REIT",    400,   26.40,  27.10,  10840.0,   280.0,  2.65,  "STOCK"),
    ]

    invested = sum(p.market_value for p in positions)
    total_pnl = sum(p.unrealized_pnl for p in positions)
    cash = 8450.0
    total = invested + cash

    return Portfolio(
        account_id="IL-DISCOUNT-MOCK-001",
        account_name="תיק השקעות אישי",
        total_value=total,
        cash_balance=cash,
        invested_value=invested,
        total_pnl=total_pnl,
        total_pnl_pct=(total_pnl / (invested - total_pnl)) * 100,
        positions=positions,
        as_of=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )


# ── Real API client (skeleton for Phase 3 live) ───────────────────────────────

class BankDiscountClient:
    """Bank Discount Open Banking client.

    In MOCK_MODE=true  → returns realistic mock data instantly.
    In MOCK_MODE=false → calls real Bank Discount PSD2 API with mTLS auth.
    """

    def __init__(self) -> None:
        """Initialise the client, creating a live httpx session if not in mock mode."""
        self.mock = MOCK_MODE
        if not self.mock:
            try:
                import httpx
                self.client = httpx.Client(
                    base_url=BASE_URL,
                    cert=(CERT_PATH, KEY_PATH),
                    timeout=30,
                    headers={
                        "client_id": CLIENT_ID,
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                )
                logger.info("BankDiscountClient initialized in LIVE mode")
            except Exception as e:
                logger.error("Failed to init live client: %s. Falling back to mock.", e)
                self.mock = True
        else:
            logger.info("BankDiscountClient initialized in MOCK mode")

    def get_portfolio(self, account_id: str | None = None) -> Portfolio:
        """Fetch current portfolio positions and values.

        Args:
            account_id: Specific account ID to fetch, or None to use the first account.

        Returns:
            A Portfolio snapshot with all positions and aggregate totals.
        """
        if self.mock:
            logger.info("Returning MOCK portfolio data")
            return _mock_portfolio()

        # ── Real API flow ──────────────────────────────────────────────────
        # Step 1: Get accounts list
        accounts_resp = self.client.get("/accounts")
        accounts_resp.raise_for_status()
        accounts = accounts_resp.json().get("accounts", [])

        if not accounts:
            raise ValueError("No accounts found in Bank Discount response")

        acct = accounts[0] if not account_id else next(
            (a for a in accounts if a["accountId"] == account_id), accounts[0]
        )
        acct_id = acct["accountId"]

        # Step 2: Get positions
        positions_resp = self.client.get(f"/accounts/{acct_id}/securities")
        positions_resp.raise_for_status()
        raw_positions = positions_resp.json().get("securities", [])

        # Step 3: Get cash balance
        balance_resp = self.client.get(f"/accounts/{acct_id}/balance")
        balance_resp.raise_for_status()
        balance = balance_resp.json()

        positions = []
        for p in raw_positions:
            qty = float(p.get("quantity", 0))
            price = float(p.get("currentPrice", 0))
            cost = float(p.get("averageCost", price))
            mval = qty * price
            pnl = mval - (qty * cost)
            positions.append(Position(
                symbol=p.get("symbol", ""),
                name=p.get("name", ""),
                quantity=qty,
                avg_cost=cost,
                current_price=price,
                market_value=mval,
                unrealized_pnl=pnl,
                pnl_pct=(pnl / (qty * cost)) * 100 if cost > 0 else 0,
                asset_type=p.get("assetType", "STOCK"),
            ))

        cash = float(balance.get("cashBalance", 0))
        invested = sum(p.market_value for p in positions)
        total_pnl = sum(p.unrealized_pnl for p in positions)

        return Portfolio(
            account_id=acct_id,
            account_name=acct.get("accountName", ""),
            total_value=invested + cash,
            cash_balance=cash,
            invested_value=invested,
            total_pnl=total_pnl,
            total_pnl_pct=(total_pnl / (invested - total_pnl)) * 100 if invested > total_pnl else 0,
            positions=positions,
            as_of=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )

    def get_transactions(self, days: int = 30) -> list[dict]:
        """Fetch recent transactions.

        Args:
            days: How many days of history to retrieve. Defaults to 30.

        Returns:
            List of transaction dicts with keys: date, type, symbol, quantity,
            price, amount.
        """
        if self.mock:
            since = datetime.now() - timedelta(days=days)
            return [
                {"date": (since + timedelta(days=2)).strftime("%Y-%m-%d"),  "type": "BUY",  "symbol": "TEVA", "quantity": 100, "price": 38.50, "amount": 3850.0},
                {"date": (since + timedelta(days=8)).strftime("%Y-%m-%d"),  "type": "BUY",  "symbol": "LUMI", "quantity": 200, "price": 22.80, "amount": 4560.0},
                {"date": (since + timedelta(days=15)).strftime("%Y-%m-%d"), "type": "SELL", "symbol": "MGDL", "quantity": 100, "price": 10.60, "amount": 1060.0},
                {"date": (since + timedelta(days=22)).strftime("%Y-%m-%d"), "type": "BUY",  "symbol": "ESLT", "quantity": 5,   "price": 590.0, "amount": 2950.0},
            ]

        # Real API
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        resp = self.client.get(f"/accounts/transactions?fromDate={since}")
        resp.raise_for_status()
        return resp.json().get("transactions", [])

    def close(self) -> None:
        """Close the underlying httpx session if running in live mode."""
        if not self.mock and hasattr(self, "client"):
            self.client.close()
