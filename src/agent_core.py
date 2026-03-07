import json, time
from datetime import datetime
from typing import Optional
import anthropic
from src.utils.config import get_config
from src.models.report import RecommendationReport, StockRecommendation, Conviction, Action

CLAUDE_MODEL = "claude-opus-4-5"

SYSTEM_PROMPT = """You are a senior Israeli equities portfolio manager with 20 years of experience on the Tel Aviv Stock Exchange (TASE). You combine rigorous quantitative analysis with deep knowledge of Israeli macroeconomics, geopolitical risk, and sector dynamics.

You think in both Hebrew market context and global macro. You understand how USD/ILS movements affect exporters vs domestic companies, how Bank of Israel decisions affect real estate and financials, and how geopolitical events create opportunity in Israeli equities.

Your recommendations are data-driven, honest about uncertainty, risk-aware, and actionable.
You NEVER hallucinate financial data. If a metric is not in the context, you omit it."""

def _build_context(portfolio, signals, macro, index_perf, news_chunks, run_type):
    holdings_text = "\n".join([
        f"  - {h.ticker} ({h.company_name}): {h.quantity} units, avg ₪{h.avg_cost_ils:.2f}, now ₪{h.current_price:.2f}, P&L {h.unrealized_pnl_pct:+.1f}%, weight {h.weight_pct:.1f}%"
        for h in sorted(portfolio.holdings, key=lambda x: x.weight_pct, reverse=True)
    ])
    held = {h.ticker for h in portfolio.holdings}
    top_bull = [s for s in signals if (s.composite_score or 0)>0.2][:8]
    top_bear = [s for s in signals if (s.composite_score or 0)<-0.2][:5]
    def sig_line(s):
        tag = " [HELD]" if s.ticker in held else ""
        flags = ", ".join(s.signal_summary) if s.signal_summary else "no flags"
        return f"  {s.ticker}{tag}: score={s.composite_score:+.2f}, RSI={s.rsi_14 or 'N/A'}, mom20d={s.momentum_20d or 'N/A'}%, [{flags}]"
    news_text = "\n\n".join([
        f"  [{i+1}] {n.get('source','?')} | {n.get('published_at','?')}\n  Tickers: {', '.join(n.get('tickers_mentioned',[]))}\n  {n.get('title','')}\n  {n.get('body','')[:300]}..."
        for i,n in enumerate(news_chunks[:6])
    ]) or "  No recent news."
    return f"""<run_context>Run: {run_type.upper()} | {datetime.now().strftime('%Y-%m-%d %H:%M IST')}</run_context>

<portfolio>
  Total: ₪{portfolio.total_value_ils:,.0f} | Cash: ₪{portfolio.cash_ils:,.0f} ({float(portfolio.cash_ils)/float(portfolio.total_value_ils)*100:.1f}%) | Day P&L: ₪{portfolio.day_pnl_ils:+,.0f} ({portfolio.day_pnl_pct:+.2f}%)
  Holdings:
{holdings_text}
</portfolio>

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
Give 8-12 recommendations. Prioritise: holdings to trim/exit, new BUY ideas from bullish signals, urgent risk flags.
</task>"""


class PortfolioAgent:
    def __init__(self):
        self._client = anthropic.Anthropic(api_key=get_config().anthropic_api_key)

    def generate_report(self, portfolio, signals, macro, index_perf, news_chunks, run_type="morning"):
        start = time.monotonic()
        context = _build_context(portfolio, signals, macro, index_perf, news_chunks, run_type)
        message = self._client.messages.create(
            model=CLAUDE_MODEL, max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=[{"role":"user","content":context}],
        )
        duration = round(time.monotonic()-start, 2)
        raw = message.content[0].text
        report = self._parse(raw, run_type)
        usage = {"prompt_tokens":message.usage.input_tokens,"completion_tokens":message.usage.output_tokens,"duration_s":duration,"model":CLAUDE_MODEL}
        return report, usage

    def _parse(self, raw, run_type):
        clean = raw.strip()
        if clean.startswith("```"): clean = clean.split("```")[1]; clean = clean[4:] if clean.startswith("json") else clean
        clean = clean.strip().rstrip("`").strip()
        try:
            data = json.loads(clean)
        except:
            return RecommendationReport(report_time=datetime.now(), run_type=run_type,
                market_summary="Parse error — check logs.", macro_outlook="",
                portfolio_risk_flags=["SYSTEM: parse error"], recommendations=[])
        recs = []
        for item in data.get("recommendations",[]):
            try:
                recs.append(StockRecommendation(
                    ticker=item["ticker"], action=Action(item["action"]),
                    conviction=Conviction(item["conviction"]), thesis=item.get("thesis",""),
                    key_risk=item.get("key_risk",""),
                    suggested_position_pct=float(item.get("suggested_position_pct",0)),
                    supporting_signals=item.get("supporting_signals",[]),
                    price_target_ils=item.get("price_target_ils"),
                ))
            except: continue
        return RecommendationReport(
            report_time=datetime.fromisoformat(data.get("report_time", datetime.now().isoformat())),
            run_type=data.get("run_type",run_type),
            market_summary=data.get("market_summary",""),
            macro_outlook=data.get("macro_outlook",""),
            portfolio_risk_flags=data.get("portfolio_risk_flags",[]),
            recommendations=recs,
        )
