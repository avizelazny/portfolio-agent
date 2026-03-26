"""Load a PortfolioSnapshot from a user-editable YAML file.

This module decouples portfolio data from Python code so positions can be
updated in portfolio.yaml without touching any source files.

Supported pricing modes (set per holding via the ``pricing`` field):
  - ``continuous``: price fetched from Globes (TASE live/last traded price)
  - ``nav``:        NAV fetched from funder.co.il (end-of-day, in agorot ÷ 100)
  - omitted:        no live fetch; uses current_price from YAML as-is

For ``nav`` holdings, both avg_cost_ils and the fetched NAV are in AGOROT
(as shown on funder.co.il and Israeli brokerage statements). portfolio_loader
divides both by 100 before storing in the Holding model.
"""
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Optional

import yaml

from src.models.market import Holding, PortfolioSnapshot


@dataclass
class InvestmentMandate:
    """Investment mandate parameters loaded from portfolio.yaml."""

    target_return_pct: float
    benchmark: str
    max_positions: int
    max_single_position_pct: float
    min_conviction: str
    cash_opportunity_cost_pct: float
    favour_conviction: bool
    notes: str = ""


def load_pending_orders(path: str = "portfolio.yaml") -> list[dict]:
    """Load pending limit orders from portfolio.yaml.

    Args:
        path: Path to the YAML portfolio file. Defaults to "portfolio.yaml".

    Returns:
        List of pending order dicts (security_id, name, action, quantity,
        limit_price, placed_date), or an empty list if none are defined.
    """
    with open(path, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    if not isinstance(doc, dict):
        return []
    return doc.get("pending_orders", []) or []


def load_mandate(path: str = "portfolio.yaml") -> InvestmentMandate:
    """Load investment mandate parameters from portfolio.yaml.

    Args:
        path: Path to the YAML portfolio file. Defaults to "portfolio.yaml".

    Returns:
        InvestmentMandate populated from the ``mandate:`` section, with
        sensible defaults if the section is absent.
    """
    with open(path, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    m = doc.get("mandate", {}) if isinstance(doc, dict) else {}
    return InvestmentMandate(
        target_return_pct=float(m.get("target_return_pct", 11.0)),
        benchmark=str(m.get("benchmark", "TA-35")),
        max_positions=int(m.get("max_positions", 8)),
        max_single_position_pct=float(m.get("max_single_position_pct", 15.0)),
        min_conviction=str(m.get("min_conviction", "MEDIUM")),
        cash_opportunity_cost_pct=float(m.get("cash_opportunity_cost_pct", 4.0)),
        favour_conviction=bool(m.get("favour_conviction", True)),
        notes=str(m.get("notes", "")),
    )

_REQUIRED_HOLDING_FIELDS = {"ticker", "company_name", "quantity", "avg_cost_ils", "current_price"}
_AGOROT_DIVISOR = Decimal("100")


def _to_decimal(value: Any, field: str, ticker: str) -> Decimal:
    """Coerce a YAML scalar to Decimal, raising ValueError on failure.

    Args:
        value: Raw value from the YAML document.
        field: Field name, used in the error message.
        ticker: Ticker symbol, used in the error message.

    Returns:
        Decimal representation of the value.

    Raises:
        ValueError: If the value cannot be converted to a valid Decimal.
    """
    try:
        return Decimal(str(value))
    except InvalidOperation:
        raise ValueError(
            f"Portfolio YAML: invalid value for '{field}' on {ticker} — "
            f"expected a number, got {value!r}"
        )


def _fetch_live_price(ticker: str, pricing: str) -> Optional[float]:
    """Dispatch a live price fetch to the appropriate connector.

    Args:
        ticker: TASE security identifier.
        pricing: Pricing mode — "continuous" (Globes) or "nav" (funder.co.il).

    Returns:
        Fetched price as float, or None if fetch is not applicable or fails.
    """
    if pricing == "continuous":
        from src.connectors.globes_connector import fetch_continuous_price
        return fetch_continuous_price(ticker)
    if pricing == "nav":
        from src.connectors.funder_connector import fetch_nav
        return fetch_nav(ticker)
    return None


def load_portfolio(path: str = "portfolio.yaml") -> PortfolioSnapshot:
    """Load a PortfolioSnapshot from a YAML file with live price enrichment.

    Reads holdings from the YAML file, fetches live prices via the configured
    connectors, then computes all derived fields (market value, unrealised P&L,
    portfolio weights).

    Live price fetch behaviour:
      - Holdings with ``pricing: continuous`` → Globes connector
      - Holdings with ``pricing: nav``        → funder.co.il connector
      - If fetch returns None                 → falls back to YAML current_price
      - If avg_cost_ils is 0.00               → P&L set to 0 (cost basis unknown)
      - For ``nav`` holdings avg_cost_ils is in AGOROT and is divided by 100

    Args:
        path: Path to the YAML portfolio file. Defaults to "portfolio.yaml"
            in the current working directory.

    Returns:
        A fully populated PortfolioSnapshot with all derived fields computed.

    Raises:
        FileNotFoundError: If the YAML file does not exist at the given path.
        ValueError: If the YAML structure is invalid, required fields are
            missing, or any numeric value cannot be parsed.
    """
    yaml_path = Path(path)
    if not yaml_path.exists():
        raise FileNotFoundError(
            f"Portfolio file not found: {yaml_path.resolve()}\n"
            "Create portfolio.yaml in the project root or pass a custom path."
        )

    with yaml_path.open(encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)

    if not isinstance(doc, dict):
        raise ValueError(
            f"Portfolio YAML must be a mapping at the top level, got {type(doc).__name__}"
        )

    raw_holdings = doc.get("holdings")
    if not raw_holdings:
        raise ValueError("Portfolio YAML: 'holdings' list is missing or empty")
    if not isinstance(raw_holdings, list):
        raise ValueError("Portfolio YAML: 'holdings' must be a list")

    # ── Phase 1: parse all holdings from YAML ─────────────────────────────────
    parsed: list[dict] = []
    for i, item in enumerate(raw_holdings):
        if not isinstance(item, dict):
            raise ValueError(
                f"Portfolio YAML: holding #{i+1} must be a mapping, got {type(item).__name__}"
            )

        missing = _REQUIRED_HOLDING_FIELDS - item.keys()
        if missing:
            ticker = item.get("ticker", f"#{i+1}")
            raise ValueError(
                f"Portfolio YAML: holding {ticker!r} is missing required fields: {sorted(missing)}"
            )

        ticker = str(item["ticker"]).strip()
        if not ticker:
            raise ValueError(f"Portfolio YAML: holding #{i+1} has an empty ticker")

        quantity = _to_decimal(item["quantity"], "quantity", ticker)
        avg_cost_raw = _to_decimal(item["avg_cost_ils"], "avg_cost_ils", ticker)
        current_price_raw = _to_decimal(item["current_price"], "current_price", ticker)
        pricing = str(item.get("pricing", "")).strip().lower()
        instrument_type = str(item.get("instrument_type", "stock")).strip().lower()

        if quantity <= 0:
            raise ValueError(f"Portfolio YAML: {ticker} quantity must be > 0, got {quantity}")

        parsed.append({
            "ticker": ticker,
            "company_name": str(item["company_name"]),
            "quantity": quantity,
            "avg_cost_raw": avg_cost_raw,
            "current_price_raw": current_price_raw,
            "pricing": pricing,
            "instrument_type": instrument_type,
        })

    # ── Phase 2: fetch live prices ─────────────────────────────────────────────
    has_live_pricing = any(p["pricing"] in ("continuous", "nav") for p in parsed)
    if has_live_pricing:
        print("  📡 Fetching live prices...")

    for p in parsed:
        pricing = p["pricing"]
        if pricing not in ("continuous", "nav"):
            continue
        fetched = _fetch_live_price(p["ticker"], pricing)
        if fetched is not None:
            # Store as Decimal; for nav, fetched is already in ILS (÷100 done in connector)
            p["current_price_raw"] = Decimal(str(fetched))

    # ── Phase 3: build Holding objects ────────────────────────────────────────
    holdings: list[Holding] = []
    for p in parsed:
        ticker = p["ticker"]
        pricing = p["pricing"]
        quantity = p["quantity"]
        current_price_raw = p["current_price_raw"]
        avg_cost_raw = p["avg_cost_raw"]

        # For nav holdings:
        #   - current_price_raw is already in ILS (funder_connector divides agorot by 100)
        #   - avg_cost_raw is in agorot as entered in portfolio.yaml → divide by 100
        if pricing == "nav":
            current_price = current_price_raw  # already ILS from connector (or 0 fallback)
            avg_cost = avg_cost_raw / _AGOROT_DIVISOR
        else:
            current_price = current_price_raw
            avg_cost = avg_cost_raw

        # Compute P&L — skip if cost basis or price unknown (zero)
        if avg_cost > 0 and current_price > 0:
            pnl_ils = (current_price - avg_cost) * quantity
            pnl_pct = Decimal(str(round(
                float((current_price - avg_cost) / avg_cost * Decimal("100")), 2
            )))
        else:
            pnl_ils = Decimal("0")
            pnl_pct = Decimal("0")

        market_value = quantity * current_price

        holdings.append(Holding(
            ticker=ticker,
            company_name=p["company_name"],
            quantity=quantity,
            avg_cost_ils=avg_cost,
            current_price=current_price,
            market_value_ils=market_value,
            unrealized_pnl_ils=pnl_ils,
            unrealized_pnl_pct=pnl_pct,
            weight_pct=Decimal("0"),  # computed below after totalling
            instrument_type=p["instrument_type"],
        ))

    cash_ils = _to_decimal(doc.get("cash_ils", "0"), "cash_ils", "portfolio")
    if cash_ils < 0:
        raise ValueError(f"Portfolio YAML: cash_ils must be >= 0, got {cash_ils}")

    invested_ils = sum(h.market_value_ils for h in holdings)
    total_value_ils = invested_ils + cash_ils

    if total_value_ils <= 0:
        raise ValueError(
            "Portfolio YAML: total portfolio value is zero — check holdings and prices"
        )

    for h in holdings:
        h.weight_pct = Decimal(str(round(
            float(h.market_value_ils) / float(total_value_ils) * 100, 2
        )))

    return PortfolioSnapshot(
        snapshot_time=datetime.now(),
        total_value_ils=total_value_ils,
        cash_ils=cash_ils,
        invested_ils=invested_ils,
        day_pnl_ils=Decimal("0"),
        day_pnl_pct=Decimal("0"),
        holdings=holdings,
    )
