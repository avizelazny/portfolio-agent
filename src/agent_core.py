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
from src.db.recommendations_db import get_decision_history
from src.utils.config import get_config
from src.utils.portfolio_loader import InvestmentMandate, load_mandate, load_pending_orders

logger = logging.getLogger(__name__)

CLAUDE_MODEL = "claude-opus-4-6"

# Maps non-standard action strings that Claude may emit to canonical Action values.
ACTION_ALIASES: dict[str, str] = {
    "EXIT": "SELL",
    "LIQUIDATE": "SELL",
    "MONITOR": "WATCH",
    "AVOID": "WATCH",
    "REDUCE": "TRIM",
    "REBALANCE": "HOLD",
    "EXECUTE": "HOLD",
    "TAKE": "TRIM",   # TAKE_PROFIT → first token "TAKE" → TRIM
    "ADD": "BUY",
    "INCREASE": "BUY",
    "ACCUMULATE": "BUY",
    "OVERWEIGHT": "BUY",
}

# Maps non-standard conviction strings to canonical Conviction values.
CONVICTION_ALIASES: dict[str, str] = {
    "MEDIUM-HIGH": "HIGH",
    "LOW-MEDIUM": "LOW",
    "MEDIUM-LOW": "LOW",
    "HIGH-MEDIUM": "HIGH",
}

def build_system_prompt(mandate: "InvestmentMandate") -> str:  # noqa: F821
    """Build the Claude system prompt with mandate parameters injected at runtime.

    Args:
        mandate: Investment mandate loaded from portfolio.yaml.

    Returns:
        Fully formatted system prompt string.
    """
    conviction_line = (
        "YES — concentrated positions in high-conviction ideas outperform over time"
        if mandate.favour_conviction
        else "NO — diversified approach across many positions"
    )
    notes_line = f"- Notes: {mandate.notes}" if mandate.notes else ""
    return f"""You are a senior Israeli equities portfolio manager with 20 years of experience on the Tel Aviv Stock Exchange (TASE). You combine rigorous quantitative analysis with deep knowledge of Israeli macroeconomics, geopolitical risk, and sector dynamics.

You think in both Hebrew market context and global macro. You understand how USD/ILS movements affect exporters vs domestic companies, how Bank of Israel decisions affect real estate and financials, and how geopolitical events create opportunity in Israeli equities.

INVESTMENT MANDATE:
- Target return: {mandate.target_return_pct}% nominal annually
- Benchmark: {mandate.benchmark}
- Max positions: {mandate.max_positions} concurrent holdings
- Max single position: {mandate.max_single_position_pct}% of portfolio
- Minimum conviction to act: {mandate.min_conviction}
- Cash opportunity cost: {mandate.cash_opportunity_cost_pct}% (current כספית yield)
- Favour conviction: {conviction_line}
{notes_line}

Underperformance vs {mandate.target_return_pct}% mandate is a risk, not just capital loss.
Minimize HOLDs — only recommend HOLD when you have a specific reason not to sell. If you have no strong view, use WATCH instead.
Size matters — HIGH conviction recommendations should reflect meaningful position sizes (5-10%), not token allocations.
Money market (כספית) at {mandate.cash_opportunity_cost_pct}% is an opportunity cost vs {mandate.target_return_pct}% target.
No unnecessary diversification — 3 great ideas beat 10 mediocre ones.

Your recommendations are data-driven, honest about uncertainty, and always referenced against the {mandate.target_return_pct}% annual mandate.
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
    transaction_context: str = "",
    live_prices_context: str = "",
    macro_extra: Optional[dict] = None,
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
        transaction_context: Optional formatted transaction history string from
            format_transactions_for_prompt(). Injected as <transaction_history>
            block so Claude knows entry timing, holding period, and cost basis.
        live_prices_context: Optional formatted live price string from
            format_live_prices_for_prompt(). Injected as <live_prices> block
            so Claude has intraday price awareness beyond the portfolio snapshot.
        macro_extra: Optional dict with additional macro context, including:
            'usdils_momentum' (dict from fetch_usdils_momentum) and
            'dividend_calendar' (list from fetch_dividend_calendar).

    Returns:
        Formatted prompt string ready to be sent as the user message.
    """
    holdings_text = "\n".join([
        f"  - {h.ticker} ({h.company_name}) [{h.instrument_type}]: {h.quantity} units, avg ₪{h.avg_cost_ils:.2f}, "
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
    tx_block = f"\n<transaction_history>\n{transaction_context}\n</transaction_history>\n" if transaction_context else ""
    live_prices_block = f"\n<live_prices>\n{live_prices_context}\n</live_prices>\n" if live_prices_context else ""

    # ── Pending orders block ──────────────────────────────────────────────────
    orders = load_pending_orders()
    if orders:
        orders_lines = "\n".join(
            f"  - {o['action']} {o['name']} ({o['security_id']}): "
            f"{o['quantity']} units @ ₪{o['limit_price']} limit (placed {o['placed_date']})"
            for o in orders
        )
        total_qty = sum(o["quantity"] for o in orders)
        pending_orders_block = (
            f"\n<pending_orders>\n"
            f"  These limit orders are currently open at the broker — "
            f"do NOT recommend actions that conflict with them:\n"
            f"{orders_lines}\n"
            f"  Total exposure: {total_qty} units of "
            f"{orders[0]['security_id']} @ ₪{orders[0]['limit_price']} limit\n"
            f"</pending_orders>"
        )
    else:
        pending_orders_block = ""

    # ── FX momentum block ──────────────────────────────────────────────────────
    _extra = macro_extra or {}
    fx = _extra.get("usdils_momentum", {})
    if fx:
        fx_block = (
            f"\n<fx_momentum>\n"
            f"  USD/ILS: {fx.get('current', 'N/A')} | "
            f"30d change: {fx.get('change_30d_pct', 'N/A'):+}% | "
            f"7d: {fx.get('change_7d_pct', 'N/A'):+}%\n"
            f"  Trend: {fx.get('trend', 'N/A')} — {fx.get('implication', '')}\n"
            f"</fx_momentum>"
        )
    else:
        fx_block = ""

    # ── Dividend calendar block ────────────────────────────────────────────────
    divs = _extra.get("dividend_calendar", [])
    if divs:
        div_lines = "\n".join(
            f"  {d['ticker']}: ex-div {d['ex_date']} (₪{d['amount']})"
            for d in divs
        )
        div_block = f"\n<dividend_calendar>\n  Upcoming ex-dividend dates (next 30 days):\n{div_lines}\n</dividend_calendar>"
    else:
        div_block = ""

    # ── Decision history block ─────────────────────────────────────────────────
    # Inject the last 4 weeks of approved/rejected decisions so Claude can see
    # what it previously recommended, what was acted on, and why things were
    # rejected — closing the Karpathy feedback loop without any schema changes.
    _history = get_decision_history(n_weeks=4)
    if _history:
        _hist_lines = "\n".join(
            f'  <decision ticker="{r["symbol"]}" action="{r["action"]}" '
            f'verdict="{"APPROVED" if r["approved"] == 1 else "REJECTED"}" '
            f'date="{r["created_at"][:10]}">\n'
            f'    {r["approval_note"] or "(no note)"}\n'
            f'  </decision>'
            for r in _history
        )
        decision_history_block = (
            f"\n<decision_history>\n{_hist_lines}\n</decision_history>"
        )
    else:
        decision_history_block = ""

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
</macro>{fx_block}{div_block}{pending_orders_block}

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
{decision_history_block}{tx_block}{live_prices_block}
<task>
Generate a {run_type} investment report. Respond ONLY with valid JSON — no preamble, no markdown, no code fences.
CRITICAL — action field MUST be exactly one of: BUY, SELL, TRIM, HOLD, WATCH. No other values. No compound values. No slashes.
CRITICAL — conviction field MUST be exactly one of: HIGH, MEDIUM, LOW. No other values.
CRITICAL JSON RULE: Hebrew fund names may contain the gershayim mark (e.g. ת"א). If you reference such names inside a JSON string value, replace the double-quote character with the Unicode gershayim ״ (U+05F4) so the JSON remains valid. Never leave an unescaped " inside a JSON string value.
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

    def __init__(self, model: str = CLAUDE_MODEL) -> None:
        """Initialise the agent and authenticate the Anthropic client.

        Args:
            model: Claude model ID to use for report generation.
                   Defaults to CLAUDE_MODEL (claude-opus-4-6).
        """
        self._client = anthropic.Anthropic(api_key=get_config().anthropic_api_key)
        self._model = model

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
        transaction_context: str = "",
        live_prices_context: str = "",
    ) -> tuple[RecommendationReport, dict]:
        """Generate a full investment report by calling the Claude API.

        Fetches USD/ILS 30-day momentum and upcoming dividend calendar
        automatically before building the context prompt.

        Args:
            portfolio: Current portfolio snapshot.
            signals: Quantitative signals for all tracked tickers.
            macro: Latest macroeconomic data.
            index_perf: Index performance dict with 'ta35' and 'ta125' keys.
            news_chunks: Recent news items to include in the prompt.
            run_type: Report cadence label, e.g. "morning" or "evening".
            performance_text: Optional historical performance summary string.
            funds_text: Optional mutual fund data string in Hebrew.
            transaction_context: Optional formatted transaction history string.
                When provided, injected into the prompt so Claude knows entry
                timing, holding period, and cost basis for each position.
            live_prices_context: Optional formatted live price string from
                format_live_prices_for_prompt(). Gives Claude intraday price
                awareness beyond the static portfolio snapshot.

        Returns:
            A tuple of (RecommendationReport, usage_dict) where usage_dict
            contains prompt_tokens, completion_tokens, duration_s, and model.
        """
        from src.connectors.macro_connector import fetch_dividend_calendar, fetch_usdils_momentum

        # Collect stock tickers from portfolio + signals for dividend calendar
        stock_tickers = [
            h.ticker for h in portfolio.holdings
            if not h.ticker.isdigit()
        ] + [
            s.ticker for s in signals
            if not s.ticker.isdigit()
        ]
        # Deduplicate while preserving order
        seen: set[str] = set()
        unique_tickers = [t for t in stock_tickers if not (t in seen or seen.add(t))]  # type: ignore[func-returns-value]

        macro_extra = {
            "usdils_momentum":   fetch_usdils_momentum(),
            "dividend_calendar": fetch_dividend_calendar(unique_tickers),
        }
        logger.info(
            "agent_core: macro_extra — FX trend=%s, %d div events",
            macro_extra["usdils_momentum"].get("trend", "N/A"),
            len(macro_extra["dividend_calendar"]),
        )

        start = time.monotonic()
        context = _build_context(
            portfolio, signals, macro, index_perf, news_chunks, run_type,
            performance_text=performance_text,
            funds_text=funds_text,
            transaction_context=transaction_context,
            live_prices_context=live_prices_context,
            macro_extra=macro_extra,
        )
        logger.info(
            "agent_core: context built — live_prices block %d chars, tx block %d chars",
            len(live_prices_context),
            len(transaction_context),
        )
        mandate = load_mandate()
        message = self._client.messages.create(
            model=self._model,
            max_tokens=8192,
            system=build_system_prompt(mandate),
            messages=[{"role": "user", "content": context}],
        )
        duration = round(time.monotonic() - start, 2)
        raw = message.content[0].text
        report = self._parse(raw, run_type)
        usage = {
            "prompt_tokens": message.usage.input_tokens,
            "completion_tokens": message.usage.output_tokens,
            "duration_s": duration,
            "model": self._model,
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
                # Extract first token — handles "/", "_", spaces so compound values
                # like "SELL / EXIT", "HOLD_OVERWEIGHT", "BUY ADD" resolve cleanly.
                raw_action = re.split(r"[/_\s]", item["action"].upper().strip())[0]
                normalized_action = ACTION_ALIASES.get(raw_action, raw_action)
                raw_conviction = re.split(r"[/_\s]", item["conviction"].upper().strip())[0]
                normalized_conviction = CONVICTION_ALIASES.get(
                    item["conviction"].upper().strip(), raw_conviction
                )
                recs.append(StockRecommendation(
                    ticker=item["ticker"],
                    action=Action(normalized_action),
                    conviction=Conviction(normalized_conviction),
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
