from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class Holding(BaseModel):
    """A single security or fund position within a portfolio."""

    ticker: str
    company_name: str
    quantity: Decimal
    avg_cost_ils: Decimal
    current_price: Decimal
    market_value_ils: Decimal
    unrealized_pnl_ils: Decimal
    unrealized_pnl_pct: Decimal
    weight_pct: Decimal


class PortfolioSnapshot(BaseModel):
    """Point-in-time snapshot of the full portfolio including all holdings."""

    snapshot_time: datetime
    total_value_ils: Decimal
    cash_ils: Decimal
    invested_ils: Decimal
    day_pnl_ils: Decimal
    day_pnl_pct: Decimal
    holdings: list[Holding]


class MacroSnapshot(BaseModel):
    """Macroeconomic indicators for a given date relevant to Israeli equities."""

    date: date
    boi_interest_rate: Decimal | None = None
    cpi_annual_pct: Decimal | None = None
    usd_ils_rate: Decimal | None = None
    eur_ils_rate: Decimal | None = None
    ta35_close: Decimal | None = None
    ta125_close: Decimal | None = None


class QuantSignals(BaseModel):
    """Quantitative signals and composite score computed for a single ticker."""

    ticker: str
    signal_time: datetime
    rsi_14: float | None = None
    macd: float | None = None
    macd_signal: float | None = None
    momentum_20d: float | None = None
    volume_anomaly: float | None = None
    pe_vs_sector: float | None = None
    week52_position: float | None = None
    composite_score: float | None = None
    signal_summary: list[str] = Field(default_factory=list)
