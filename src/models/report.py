from datetime import datetime
from enum import Enum

from pydantic import BaseModel


class Action(str, Enum):
    """Possible recommendation actions for a security."""

    BUY = "BUY"
    SELL = "SELL"
    REDUCE = "REDUCE"
    TRIM = "TRIM"
    HOLD = "HOLD"
    WATCH = "WATCH"


class Conviction(str, Enum):
    """Analyst conviction level for a recommendation."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class StockRecommendation(BaseModel):
    """A single investment recommendation for one security or fund."""

    ticker: str
    action: Action
    conviction: Conviction
    thesis: str
    key_risk: str
    suggested_position_pct: float
    supporting_signals: list[str]
    price_target_ils: float | None = None


class RecommendationReport(BaseModel):
    """Full investment report produced by the portfolio agent for one run."""

    report_time: datetime
    run_type: str
    market_summary: str
    macro_outlook: str
    portfolio_risk_flags: list[str]
    recommendations: list[StockRecommendation]

    def buys(self) -> list[StockRecommendation]:
        """Return all BUY recommendations."""
        return [r for r in self.recommendations if r.action == Action.BUY]

    def sells(self) -> list[StockRecommendation]:
        """Return all SELL recommendations."""
        return [r for r in self.recommendations if r.action == Action.SELL]

    def holds(self) -> list[StockRecommendation]:
        """Return all HOLD recommendations."""
        return [r for r in self.recommendations if r.action == Action.HOLD]

    def high_conviction(self) -> list[StockRecommendation]:
        """Return all HIGH conviction recommendations regardless of action."""
        return [r for r in self.recommendations if r.conviction == Conviction.HIGH]
