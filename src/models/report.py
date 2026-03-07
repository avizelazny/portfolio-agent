from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel

class Action(str, Enum):
    BUY="BUY"; SELL="SELL"; HOLD="HOLD"; WATCH="WATCH"

class Conviction(str, Enum):
    HIGH="HIGH"; MEDIUM="MEDIUM"; LOW="LOW"

class StockRecommendation(BaseModel):
    ticker: str; action: Action; conviction: Conviction; thesis: str; key_risk: str
    suggested_position_pct: float; supporting_signals: list[str]; price_target_ils: Optional[float]=None

class RecommendationReport(BaseModel):
    report_time: datetime; run_type: str; market_summary: str; macro_outlook: str
    portfolio_risk_flags: list[str]; recommendations: list[StockRecommendation]
    def buys(self): return [r for r in self.recommendations if r.action==Action.BUY]
    def sells(self): return [r for r in self.recommendations if r.action==Action.SELL]
    def holds(self): return [r for r in self.recommendations if r.action==Action.HOLD]
    def high_conviction(self): return [r for r in self.recommendations if r.conviction==Conviction.HIGH]
