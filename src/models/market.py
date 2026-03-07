from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from pydantic import BaseModel, Field

class Holding(BaseModel):
    ticker: str; company_name: str; quantity: Decimal; avg_cost_ils: Decimal
    current_price: Decimal; market_value_ils: Decimal; unrealized_pnl_ils: Decimal
    unrealized_pnl_pct: Decimal; weight_pct: Decimal

class PortfolioSnapshot(BaseModel):
    snapshot_time: datetime; total_value_ils: Decimal; cash_ils: Decimal
    invested_ils: Decimal; day_pnl_ils: Decimal; day_pnl_pct: Decimal
    holdings: list[Holding]

class MacroSnapshot(BaseModel):
    date: date; boi_interest_rate: Optional[Decimal]=None; cpi_annual_pct: Optional[Decimal]=None
    usd_ils_rate: Optional[Decimal]=None; eur_ils_rate: Optional[Decimal]=None
    ta35_close: Optional[Decimal]=None; ta125_close: Optional[Decimal]=None

class QuantSignals(BaseModel):
    ticker: str; signal_time: datetime; rsi_14: Optional[float]=None
    macd: Optional[float]=None; macd_signal: Optional[float]=None
    momentum_20d: Optional[float]=None; volume_anomaly: Optional[float]=None
    pe_vs_sector: Optional[float]=None; week52_position: Optional[float]=None
    composite_score: Optional[float]=None; signal_summary: list[str]=Field(default_factory=list)
