import json
import logging
import re
import time
from datetime import datetime
from typing import Optional

import anthropic

from src.models.market import MacroSnapshot, PortfolioSnapshot, QuantSignals
from src.models.report import (
    Action,
    Conviction,
    RecommendationReport,
    StockRecommendation,
)
from src.utils.config import get_config

logger = logging.getLogger(__name__)

CLAUDE_MODEL = "claude-opus-4-6"

SYSTEM_PROMPT = """You are a senior Israeli equities portfolio manager with 20 years of experience on the Tel Aviv Stock Exchange (TASE). You combine rigorous quantitative analysis with deep knowledge of Israeli macroeconomics, geopolitical risk, and sector dynamics.

You think in both Hebrew market context and global macro. You understand how USD/ILS movements affect exporters vs domestic companies, how Bank of Israel decisions affect real estate and financials, and how geopolitical events create opportunity in Israeli equities.

You also manage a mutual fund portfolio (קרנות נאמנות) alongside individual stocks. You analyze fund NAV trends, YTD performance, and rebalancing opportunities between tracking funds (TTF), money market funds (כספיות), and defense/sector funds.

Your recommendations are data-driven, honest about uncertainty, risk-aware, and actionable.
You NEVER hallucinate financial data. If a metric is not in the context, you omit it."""


def _build_context(
    portfolio: PortfolioSnapshot,
    signals: list[QuantSignals],
    macro: MacroSnapshot,
    index_perf: dict,
    news_chunks: list[dict],
    run_type: str,
    performance_text: Optional[str] = None,
    funds_text: Optional[str] = None,
) -> str:
    """Build the prompt context string sent to the Claude model.

    Assembles portfolio holdings, quant signals, macro data, news, and
    performance history into a structured XML-like prompt block.

    Args:
        portfolio: Current portfolio snapshot with holdings and cash.
        signals: List of quantitative signals per ticker.
        macro: Latest macroeconomic snapshot (BOI rate, CPI, FX).
        index_perf: Dict with keys 'ta35' and 'ta125', each containing 'change_pct'.
        news_chunks: List of news item dicts with keys: source, published_at,
            tickers_mentioned, title, body.
        run_type: Report cadence label, e.g. "morning" or "evening".
        performance_text: Optional formatted string of historical performance.
        funds_text: Optional formatted string of mutual fund data in Hebrew.

    Returns:
        Formatted prompt string ready to be sent as the user message.
    """
    holdings_text = "\n".join([
        f"  - {h.ticker} ({h.company_name}): {h.quantity} units, avg ₪{h.avg_cost_ils:.2f}, "
        f"now ₪{h.current_price:.2f}, P&L {h.unrealized_pnl_pct:+.1f}%, weight {h.weight_pct:.1f}%"
        for h in sorted(portfolio.holdings, key=lambda x: x.weight_pct, reverse=True)
    ])
    held = {h.ticker for h in portfolio.holdings}
    top_bull = [s for s in signals if (s.composite_score or 0) > 0.2][:8]
    top_bear = [s for s in signals if (s.composite_score or 0) < -0.2][:5]

    def sig_line(s: QuantSignals) -> str:
        """Format a single quant signal as a human-readable line."""
        tag = " [HELD]" if s.ticker in held else ""
        flags = ", ".join(s.signal_summary) if s.signal_summary else "no flags"
        return (
            f"  {s.ticker}{tag}: score={s.composite_score:+.2f}, "
            f"RSI={s.rsi_14 or 'N/A'}, mom20d={s.momentum_20d or 'N/A'}%, [{flags}]"
        )

    news_text = "\n\n".join([
        f"  [{i+1}] {n.get('source','?')} | {n.get('published_at','?')}\n"
        f"  Tickers: {', '.join(n.get('tickers_mentioned', []))}\n"
        f"  {n.get('title','')}\n"
        f"  {n.get('body','')[:300]}..."
        for i, n in enumerate(news_chunks[:6])
    ]) or "  No recent news."

    funds_block = funds_text or "נתוני קרנות נאמנות לא זמינים."

    return f"""<run_context>Run: {run_type.upper()} | {datetime.now().strftime('%Y-%m-%d %H:%M IST')}</run_context>

<portfolio>
  Total: ₪{portfolio.total_value_ils:,.0f} | Cash: ₪{portfolio.cash_ils:,.0f} ({float(portfolio.cash_ils)/float(portfolio.total_value_ils)*100:.1f}%) | Day P&L: ₪{portfolio.day_pnl_ils:+,.0f} ({portfolio.day_pnl_pct:+.2f}%)
  Holdings:
{holdings_text}
</portfolio>

<mutual_funds>
{funds_block}
</mutual_funds>

<macro>
  BOI Rate: {macro.boi_interest_rate or 'N/A'}% | CPI: {macro.cpi_annual_pct or 'N/A'}% | USD/ILS: {macro.usd_ils_rate or 'N/A'}
  TA-35: {index_perf.get('ta35',{}).get('change_pct','N/A'):+}% | TA-125: {index_perf.get('ta125',{}).get('change_pct','N/A'):+}%
</macro>

<signals>
  BULLISH:\n{chr(10).join(sig_line(s) for s in top_bull) or '  None'}
  BEARISH:\n{chr(10).join(sig_line(s) for s in top_bear) or '  None'}
</signals>

<news>
{news_text}
</news>

<performance_history>
{performance_text or "No performance history yet — this is an early run."}
</performance_history>

<task>
Generate a {run_type} investment report. Respond ONLY with valid JSON — no preamble, no markdown.
{{
  "report_time": "{datetime.now().isoformat()}",
  "run_type": "{run_type}",
  "market_summary": "2 sentences on today's market",
  "macro_outlook": "2-3 sentences on macro context for Israeli equities",
  "portfolio_risk_flags": ["flag1", "flag2"],
  "recommendations": [
    {{
      "ticker": "XXXX",
      "action": "BUY",
      "conviction": "HIGH",
      "thesis": "3-5 sentences with specific data points",
      "key_risk": "1-2 sentences on what invalidates this thesis",
      "suggested_position_pct": 5.0,
      "supporting_signals": ["RSI oversold at 28", "strong momentum"],
      "price_target_ils": 123.0
    }}
  ]
}}
Give 8-12 recommendations covering BOTH individual stocks AND mutual fund rebalancing.
For funds: note if a fund is underperforming its benchmark, has high fees vs alternatives, or if the TA-35 drop today (-2.98%) suggests rebalancing between the two TA-35 tracking funds.
Prioritise: holdings to trim/exit, new BUY ideas from bullish signals, fund rebalancing opportunities, urgent risk flags.
</task>"""


class PortfolioAgent:
    """Autonomous portfolio analysis agent powered by Claude.

    Fetches market context, calls the Claude API with a structured prompt,
    and parses the response into a typed RecommendationReport.
    """

    def __init__(self) -> None:
        """Initialise the agent and authenticate the Anthropic client."""
        self._client = anthropic.Anthropic(api_key=get_config().anthropic_api_key)

    def generate_report(
        self,
        portfolio: PortfolioSnapshot,
        signals: list[QuantSignals],
        macro: MacroSnapshot,
        index_perf: dict,
        news_chunks: list[dict],
        run_type: str = "morning",
        performance_text: Optional[str] = None,
        funds_text: Optional[str] = None,
    ) -> tuple[RecommendationReport, dict]:
        """Generate a full investment report by calling the Claude API.

        Args:
            portfolio: Current portfolio snapshot.
            signals: Quantitative signals for all tracked tickers.
            macro: Latest macroeconomic data.
            index_perf: Index performance dict with 'ta35' and 'ta125' keys.
            news_chunks: Recent news items to include in the prompt.
            run_type: Report cadence label, e.g. "morning" or "evening".
            performance_text: Optional historical performance summary string.
            funds_text: Optional mutual fund data string in Hebrew.

        Returns:
            A tuple of (RecommendationReport, usage_dict) where usage_dict
            contains prompt_tokens, completion_tokens, duration_s, and model.
        """
        start = time.monotonic()
        context = _build_context(
            portfolio, signals, macro, index_perf, news_chunks, run_type,
            performance_text=performance_text,
            funds_text=funds_text,
        )
        message = self._client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": context}],
        )
        duration = round(time.monotonic() - start, 2)
        raw = message.content[0].text
        report = self._parse(raw, run_type)
        usage = {
            "prompt_tokens": message.usage.input_tokens,
            "completion_tokens": message.usage.output_tokens,
            "duration_s": duration,
            "model": CLAUDE_MODEL,
        }
        return report, usage

    def _parse(self, raw: str, run_type: str) -> RecommendationReport:
        """Parse the raw Claude response string into a RecommendationReport.

        Strips markdown code fences and extracts the first JSON object from
        the response, then maps it onto typed Pydantic models.

        Args:
            raw: Raw text response from the Claude API.
            run_type: Report cadence label used as fallback if not in JSON.

        Returns:
            A RecommendationReport instance. Returns a minimal error report
            if JSON parsing fails.
        """
        clean = raw.strip()
        clean = re.sub(r"^```(?:json)?\s*", "", clean, flags=re.MULTILINE)
        clean = re.sub(r"```\s*$", "", clean, flags=re.MULTILINE)
        clean = clean.strip()
        match = re.search(r"\{.*\}", clean, re.DOTALL)
        if match:
            clean = match.group(0)
        try:
            data = json.loads(clean)
        except json.JSONDecodeError as e:
            logger.warning("JSON parse failed: %s", e)
            logger.warning("Raw response (first 600 chars):\n%s", raw[:600])
            return RecommendationReport(
                report_time=datetime.now(),
                run_type=run_type,
                market_summary="Parse error - check logs.",
                macro_outlook="",
                portfolio_risk_flags=["SYSTEM: parse error"],
                recommendations=[],
            )
        recs = []
        for item in data.get("recommendations", []):
            try:
                recs.append(StockRecommendation(
                    ticker=item["ticker"],
                    action=Action(item["action"]),
                    conviction=Conviction(item["conviction"]),
                    thesis=item.get("thesis", ""),
                    key_risk=item.get("key_risk", ""),
                    suggested_position_pct=float(item.get("suggested_position_pct", 0)),
                    supporting_signals=item.get("supporting_signals", []),
                    price_target_ils=item.get("price_target_ils"),
                ))
            except (KeyError, ValueError) as rec_err:
                logger.warning("Skipping rec %s: %s", item.get("ticker", "?"), rec_err)
                continue
        return RecommendationReport(
            report_time=datetime.fromisoformat(
                data.get("report_time", datetime.now().isoformat())
            ),
            run_type=data.get("run_type", run_type),
            market_summary=data.get("market_summary", ""),
            macro_outlook=data.get("macro_outlook", ""),
            portfolio_risk_flags=data.get("portfolio_risk_flags", []),
            recommendations=recs,
        )
