"""
Portfolio Agent — Demo Run
===========================
Runs the full AI pipeline with realistic mock data.
No real bank or TASE data needed — just your Anthropic API key.

Usage:
    python demo_run.py

What happens:
  1. Creates a realistic mock Israeli portfolio (TEVA, NICE, CHKP, etc.)
  2. Generates mock market data for the whole TA-125 universe
  3. Runs the quant engine (RSI, MACD, momentum, signals)
  4. Calls Claude Opus 4 to generate investment recommendations
  5. Renders a beautiful HTML report
  6. Saves it to the reports/ folder — open in your browser!
  7. Sends it to MailHog (if running) — view at http://localhost:8025
"""

import os, sys
from datetime import datetime, date
from decimal import Decimal

# Load .env automatically
from dotenv import load_dotenv
load_dotenv()

def check_api_key():
    key = os.getenv("ANTHROPIC_API_KEY","")
    if not key or key == "PASTE_YOUR_KEY_HERE":
        print("\n" + "="*60)
        print("  ❌  ANTHROPIC_API_KEY not set!")
        print()
        print("  Please open the .env file and replace:")
        print("  PASTE_YOUR_KEY_HERE")
        print("  with your actual key from console.anthropic.com")
        print("="*60 + "\n")
        sys.exit(1)

def make_portfolio():
    from src.models.market import PortfolioSnapshot, Holding
    holdings = [
        Holding(ticker="TEVA",  company_name="Teva Pharmaceutical",   quantity=Decimal("500"),  avg_cost_ils=Decimal("32.50"), current_price=Decimal("38.20"), market_value_ils=Decimal("19100"), unrealized_pnl_ils=Decimal("2850"),  unrealized_pnl_pct=Decimal("17.5"), weight_pct=Decimal("19.1")),
        Holding(ticker="NICE",  company_name="NICE Systems",           quantity=Decimal("50"),   avg_cost_ils=Decimal("380.00"),current_price=Decimal("420.00"),market_value_ils=Decimal("21000"), unrealized_pnl_ils=Decimal("2000"),  unrealized_pnl_pct=Decimal("10.5"), weight_pct=Decimal("21.0")),
        Holding(ticker="CHKP",  company_name="Check Point Software",   quantity=Decimal("30"),   avg_cost_ils=Decimal("620.00"),current_price=Decimal("590.00"),market_value_ils=Decimal("17700"), unrealized_pnl_ils=Decimal("-900"),  unrealized_pnl_pct=Decimal("-4.8"), weight_pct=Decimal("17.7")),
        Holding(ticker="LUMI",  company_name="Bank Leumi",             quantity=Decimal("800"),  avg_cost_ils=Decimal("22.00"), current_price=Decimal("25.80"), market_value_ils=Decimal("20640"), unrealized_pnl_ils=Decimal("3040"),  unrealized_pnl_pct=Decimal("17.3"), weight_pct=Decimal("20.6")),
        Holding(ticker="ICL",   company_name="ICL Group",              quantity=Decimal("1000"), avg_cost_ils=Decimal("8.50"),  current_price=Decimal("7.80"),  market_value_ils=Decimal("7800"),  unrealized_pnl_ils=Decimal("-700"),  unrealized_pnl_pct=Decimal("-8.2"), weight_pct=Decimal("7.8")),
        Holding(ticker="ESLT",  company_name="Elbit Systems",          quantity=Decimal("25"),   avg_cost_ils=Decimal("700.00"),current_price=Decimal("850.00"),market_value_ils=Decimal("21250"), unrealized_pnl_ils=Decimal("3750"),  unrealized_pnl_pct=Decimal("21.4"), weight_pct=Decimal("13.8")),
    ]
    return PortfolioSnapshot(
        snapshot_time=datetime.now(), total_value_ils=Decimal("107490"),
        cash_ils=Decimal("107490")-sum(h.market_value_ils for h in holdings),
        invested_ils=sum(h.market_value_ils for h in holdings),
        day_pnl_ils=Decimal("1356"), day_pnl_pct=Decimal("1.28"), holdings=holdings,
    )

def make_ohlcv(ticker, base=100.0, days=60):
    import random, numpy as np
    random.seed(hash(ticker) % 9999)
    bars = []; price = base
    for _ in range(days):
        change = random.gauss(0.001, 0.018)
        price = max(price*(1+change), 1.0)
        bars.append({
            "date": str(date.today()), "open": round(price*0.998,2),
            "high": round(price*1.01,2), "low": round(price*0.99,2),
            "close": round(price,2), "volume": max(int(random.gauss(400000,100000)),10000)
        })
    return bars

def make_macro():
    from src.models.market import MacroSnapshot
    return MacroSnapshot(date=date.today(), boi_interest_rate=Decimal("4.50"),
        cpi_annual_pct=Decimal("3.2"), usd_ils_rate=Decimal("3.72"),
        eur_ils_rate=Decimal("4.05"), ta35_close=Decimal("2082"), ta125_close=Decimal("1645"))

def main():
    print("\n" + "="*62)
    print("  🚀  Portfolio Agent — Demo Run")
    print("="*62)

    check_api_key()

    # Step 1 — mock portfolio
    print("\n[1/5] 🏦  Building mock Israeli portfolio...")
    portfolio = make_portfolio()
    print(f"      ₪{portfolio.total_value_ils:,.0f} total | {len(portfolio.holdings)} holdings | ₪{portfolio.cash_ils:,.0f} cash")
    for h in portfolio.holdings:
        pnl_arrow = "📈" if h.unrealized_pnl_ils >= 0 else "📉"
        print(f"      {pnl_arrow} {h.ticker:<6} {h.company_name:<28} {h.unrealized_pnl_pct:+.1f}%")

    # Step 2 — mock market data
    print("\n[2/5] 📊  Generating market data for TA-125 universe...")
    all_tickers = [
        "TEVA","NICE","CHKP","LUMI","ICL","ESLT","BEZQ","POLI","BRMG",
        "SPNS","KCHD","SANO","AZRG","AMOT","IGLD","ENLT","NWRL","MFON",
        "MTRX","BIDI","FIBI","MISH","ORION","RDWR","PMCN","CEVA","GILT"
    ]
    ohlcv_data = {t: make_ohlcv(t, base=50+hash(t)%400, days=60) for t in all_tickers}
    print(f"      Generated 60 days × {len(all_tickers)} tickers")

    # Step 3 — quant signals
    print("\n[3/5] 🔢  Computing quant signals (RSI, MACD, momentum)...")
    from src.quant_engine import QuantEngine
    sector_pes = {"Pharma":18.5,"Technology":25.0,"Banks":10.0,"Materials":14.0,"Telecom":12.0,"Defense":22.0}
    engine = QuantEngine(sector_pe_medians=sector_pes)
    tickers_data = {t:{"bars":ohlcv_data[t],"info":None} for t in all_tickers}
    signals = engine.compute_all(tickers_data)
    bullish = [s for s in signals if (s.composite_score or 0)>0.2]
    bearish = [s for s in signals if (s.composite_score or 0)<-0.2]
    print(f"      {len(bullish)} bullish signals, {len(bearish)} bearish signals")
    print(f"      Top 3: {', '.join(f'{s.ticker}({s.composite_score:+.2f})' for s in signals[:3])}")

    # Step 4 — Claude Opus 4
    print("\n[4/5] 🤖  Calling Claude Opus 4 for recommendations...")
    print("      (This takes 15-30 seconds — Claude is thinking...)")
    from src.agent_core import PortfolioAgent
    macro = make_macro()
    index_perf = {"ta35":{"change_pct":0.82},"ta125":{"change_pct":0.61}}
    news = [
        {"source":"Globes","title":"Teva signs $2.3B biosimilar licensing deal with European partner",
         "body":"Teva Pharmaceutical announced a landmark licensing agreement for its biosimilar portfolio, expected to generate $400M in annual revenue by 2026. The deal covers 14 European markets and strengthens Teva's position in the growing biosimilars segment.",
         "published_at":datetime.now().isoformat(),"tickers_mentioned":["TEVA"]},
        {"source":"TheMarker","title":"Bank of Israel signals possible rate cut in Q2 2025",
         "body":"BOI Governor Amir Yaron hinted at a potential interest rate reduction if inflation continues its downward trend. Markets reacted positively, with banking stocks leading gains. Analysts expect the cut could be 25bps as early as April.",
         "published_at":datetime.now().isoformat(),"tickers_mentioned":["LUMI","POLI","FIBI"]},
        {"source":"Calcalist","title":"Elbit Systems wins $1.2B IDF contract for drone systems",
         "body":"Elbit Systems secured a major multi-year contract with the Israeli Defense Forces for next-generation drone surveillance systems. The contract is expected to contribute significantly to backlog and revenue visibility through 2027.",
         "published_at":datetime.now().isoformat(),"tickers_mentioned":["ESLT"]},
    ]
    agent = PortfolioAgent()
    report, usage = agent.generate_report(portfolio, signals, macro, index_perf, news, run_type="morning")
    print(f"      ✅ Done! {len(report.recommendations)} recommendations generated")
    print(f"         Tokens used: {usage['prompt_tokens']:,} in / {usage['completion_tokens']:,} out")
    print(f"         Time: {usage['duration_s']}s | Model: {usage['model']}")

    # Step 5 — render and save
    print("\n[5/5] 📧  Rendering report...")
    from src.report_renderer import render_html_report, save_report_locally, send_email_report
    html = render_html_report(report, "demo-001")
    local_path = save_report_locally(html, report)
    send_email_report(html, report)

    # Print recommendations summary
    print("\n" + "─"*62)
    print("  RECOMMENDATIONS SUMMARY")
    print("─"*62)
    for rec in report.recommendations:
        icons = {"BUY":"📈","SELL":"📉","HOLD":"➡️","WATCH":"👁"}
        conv_icons = {"HIGH":"🔴","MEDIUM":"🟡","LOW":"⚪"}
        print(f"\n  {icons.get(rec.action.value,'?')} {rec.action.value:<5} {rec.ticker:<6} "
              f"{conv_icons.get(rec.conviction.value,'')} {rec.conviction.value}")
        print(f"     {rec.thesis[:110]}...")
        if rec.suggested_position_pct > 0:
            print(f"     Position: {rec.suggested_position_pct}%", end="")
            if rec.price_target_ils: print(f"  |  Target: ₪{rec.price_target_ils:.2f}", end="")
            print()

    if report.portfolio_risk_flags:
        print(f"\n  ⚠️  Risk flags:")
        for f in report.portfolio_risk_flags:
            print(f"     • {f}")

    print("\n" + "="*62)
    print("  ✅  DEMO COMPLETE!")
    print()
    print(f"  📄 Open your report:")
    print(f"     {local_path}")
    print()
    print("  📧 Or view the email version:")
    print("     http://localhost:8025  (if MailHog is running)")
    print("="*62 + "\n")

if __name__ == "__main__":
    main()
