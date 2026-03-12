from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class RecommendationRecord(BaseModel):
    """A single recommendation saved to the DB."""

    symbol: str
    action: str  # BUY / SELL / HOLD / WATCH — see Action enum in models/report.py
    conviction: str  # HIGH / MEDIUM / LOW — see Conviction enum in models/report.py
    thesis: str
    key_risk: str
    price_entry: Decimal | None = None  # price at time of recommendation
    price_target: Decimal | None = None
    run_type: str = "morning"
    ta35_at_entry: Decimal | None = None  # TA-35 level at entry (auto-filled)


class ApprovalUpdate(BaseModel):
    """What the user records when they approve/reject a recommendation."""

    rec_id: int
    approved: bool
    actual_price: Decimal | None = None  # price they actually traded at
    quantity: int | None = None  # how many units
    note: str = ""


class OutcomeUpdate(BaseModel):
    """What the price updater fills in automatically."""

    rec_id: int
    price_current: Decimal
    ta35_current: Decimal | None = None
    closed: bool = False  # True = position manually exited


class PerformanceSummary(BaseModel):
    """Aggregated stats fed back into Claude's prompt."""

    period_days: int
    total_recs: int
    approved_recs: int
    successful_recs: int
    success_rate: float  # % of approved recs that beat benchmark

    avg_return_pct: float
    avg_benchmark_pct: float = 0.0  # avg TA-35 return over same periods
    avg_alpha: float = 0.0  # avg_return - avg_benchmark

    best_trade: str | None = None
    worst_trade: str | None = None

    # Conviction breakdown — is HIGH actually better than MEDIUM?
    high_conv_success: float = 0.0
    med_conv_success: float = 0.0
    low_conv_success: float = 0.0

    # Open positions mark-to-market
    open_positions: int = 0
    open_unrealized_pct: float = 0.0

    # Pattern that worked best (filled by Phase E analyzer)
    top_pattern: str | None = None
