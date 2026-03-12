"""
Portfolio Agent — Demo Run
===========================
Full pipeline with real fund positions + mock stock portfolio.
NAV prices are stored as actual shekel values (funder.co.il values ÷ 10).
"""
import logging
import os
import random
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal

import numpy as np
from dotenv import load_dotenv

# Silence DB connection errors when running locally without PostgreSQL.
# Must be set before importing the db module so suppression takes effect.
logging.getLogger("src.db.recommendations_db").setLevel(logging.CRITICAL)

from src.agent_core import PortfolioAgent
from src.db.recommendations_db import (
    format_for_prompt,
    get_performance_summary,
    init_recommendations_table,
    save_recommendation,
)
from src.models.market import Holding, MacroSnapshot, PortfolioSnapshot
from src.models.recommendation import RecommendationRecord
from src.quant_engine import QuantEngine
from src.report_renderer import render_html_report, save_report_locally, send_email_report

logger = logging.getLogger(__name__)

# TA-125 mock universe — tickers that go through the full quant pipeline
ALL_TICKERS: list[str] = [
    "TEVA", "NICE", "CHKP", "LUMI", "ICL", "ESLT", "BEZQ", "POLI", "BRMG",
    "SPNS", "KCHD", "SANO", "AZRG", "AMOT", "IGLD", "ENLT", "NWRL", "MFON",
    "MTRX", "BIDI", "FIBI", "MISH", "ORION", "RDWR", "PMCN", "CEVA", "GILT",
]

# Watchlist — tickers monitored but not yet held; tagged [WATCH] in signals
WATCHLIST_TICKERS: list[str] = ["SMSH"]


def check_api_key() -> None:
    """Verify that ANTHROPIC_API_KEY is set in the environment.

    Prints a user-friendly error message and exits with code 1 if the key
    is missing or still set to the placeholder value.
    """
    key = os.getenv("ANTHROPIC_API_KEY", "")
    if not key or key == "PASTE_YOUR_KEY_HERE":
        print("\n" + "=" * 60)
        print("  ❌  ANTHROPIC_API_KEY not set!")
        print("  Please open the .env file and set your key.")
        print("=" * 60 + "\n")
        sys.exit(1)


def make_portfolio() -> PortfolioSnapshot:
    """Build a mock Israeli portfolio with stock and mutual fund positions.

    Stock positions are illustrative. Mutual fund positions reflect real
    holdings with NAV prices sourced from funder.co.il (stored as value ÷ 10).
    Portfolio weights are computed dynamically from market values.

    Returns:
        A PortfolioSnapshot with all holdings and aggregated totals.
    """
    stock_holdings = [
        Holding(ticker="TEVA",  company_name="Teva Pharmaceutical",  quantity=Decimal("500"),  avg_cost_ils=Decimal("32.50"),  current_price=Decimal("38.20"),  market_value_ils=Decimal("19100"),  unrealized_pnl_ils=Decimal("2850"),   unrealized_pnl_pct=Decimal("17.5"),  weight_pct=Decimal("0.0")),
        Holding(ticker="NICE",  company_name="NICE Systems",          quantity=Decimal("50"),   avg_cost_ils=Decimal("380.00"), current_price=Decimal("420.00"),  market_value_ils=Decimal("21000"),  unrealized_pnl_ils=Decimal("2000"),   unrealized_pnl_pct=Decimal("10.5"),  weight_pct=Decimal("0.0")),
        Holding(ticker="CHKP",  company_name="Check Point Software",  quantity=Decimal("30"),   avg_cost_ils=Decimal("620.00"), current_price=Decimal("590.00"),  market_value_ils=Decimal("17700"),  unrealized_pnl_ils=Decimal("-900"),   unrealized_pnl_pct=Decimal("-4.8"),  weight_pct=Decimal("0.0")),
        Holding(ticker="LUMI",  company_name="Bank Leumi",            quantity=Decimal("800"),  avg_cost_ils=Decimal("22.00"),  current_price=Decimal("25.80"),   market_value_ils=Decimal("20640"),  unrealized_pnl_ils=Decimal("3040"),   unrealized_pnl_pct=Decimal("17.3"),  weight_pct=Decimal("0.0")),
        Holding(ticker="ICL",   company_name="ICL Group",             quantity=Decimal("1000"), avg_cost_ils=Decimal("8.50"),   current_price=Decimal("7.80"),    market_value_ils=Decimal("7800"),   unrealized_pnl_ils=Decimal("-700"),   unrealized_pnl_pct=Decimal("-8.2"),  weight_pct=Decimal("0.0")),
        Holding(ticker="ESLT",  company_name="Elbit Systems",         quantity=Decimal("25"),   avg_cost_ils=Decimal("700.00"), current_price=Decimal("850.00"),  market_value_ils=Decimal("21250"),  unrealized_pnl_ils=Decimal("3750"),   unrealized_pnl_pct=Decimal("21.4"),  weight_pct=Decimal("0.0")),
    ]

    fund_holdings = [
        Holding(ticker="FUND-5136544", company_name="מיטב כספית שקלית כשרה",   quantity=Decimal("61078"),  avg_cost_ils=Decimal("11.0414"), current_price=Decimal("11.4655"), market_value_ils=Decimal("700290"),   unrealized_pnl_ils=Decimal("25903"),   unrealized_pnl_pct=Decimal("3.84"),  weight_pct=Decimal("0.0")),
        Holding(ticker="FUND-5130661", company_name="הראל מחקה ת\"א 35",        quantity=Decimal("15842"),  avg_cost_ils=Decimal("2.4497"),  current_price=Decimal("2.4497"),  market_value_ils=Decimal("38808"),    unrealized_pnl_ils=Decimal("0"),      unrealized_pnl_pct=Decimal("0.0"),   weight_pct=Decimal("0.0")),
        Holding(ticker="FUND-5109418", company_name="תכלית TTF ת\"א 35",        quantity=Decimal("11450"),  avg_cost_ils=Decimal("2.6402"),  current_price=Decimal("2.5841"),  market_value_ils=Decimal("29588"),    unrealized_pnl_ils=Decimal("-642"),   unrealized_pnl_pct=Decimal("-2.12"), weight_pct=Decimal("0.0")),
        Holding(ticker="FUND-5134556", company_name="תכלית TTF Semiconductor",  quantity=Decimal("16416"),  avg_cost_ils=Decimal("1.6704"),  current_price=Decimal("1.6244"),  market_value_ils=Decimal("26666"),    unrealized_pnl_ils=Decimal("-755"),   unrealized_pnl_pct=Decimal("-2.75"), weight_pct=Decimal("0.0")),
        Holding(ticker="FUND-5142088", company_name="קסם KTF ביטחוניות",        quantity=Decimal("35260"),  avg_cost_ils=Decimal("1.1212"),  current_price=Decimal("1.1212"),  market_value_ils=Decimal("39534"),    unrealized_pnl_ils=Decimal("0"),      unrealized_pnl_pct=Decimal("0.0"),   weight_pct=Decimal("0.0")),
        Holding(ticker="FUND-5141882", company_name="תכלית TTF ביטחוניות",      quantity=Decimal("15494"),  avg_cost_ils=Decimal("1.2983"),  current_price=Decimal("1.4312"),  market_value_ils=Decimal("22175"),    unrealized_pnl_ils=Decimal("2059"),   unrealized_pnl_pct=Decimal("10.24"), weight_pct=Decimal("0.0")),
    ]

    all_holdings = stock_holdings + fund_holdings
    total = sum(h.market_value_ils for h in all_holdings)
    for h in all_holdings:
        h.weight_pct = Decimal(str(round(float(h.market_value_ils) / float(total) * 100, 2)))

    return PortfolioSnapshot(
        snapshot_time=datetime.now(),
        total_value_ils=total,
        cash_ils=Decimal("0"),
        invested_ils=total,
        day_pnl_ils=Decimal("1356"),
        day_pnl_pct=Decimal("0.0"),
        holdings=all_holdings,
    )


def make_ohlcv(ticker: str, base: float = 100.0, days: int = 60) -> list[dict]:
    """Generate synthetic OHLCV bars for a given ticker using a random walk.

    Prices follow a log-normal random walk seeded deterministically from the
    ticker name, so results are reproducible across runs. Dates run backwards
    from today so bar[0] is the oldest and bar[-1] is the most recent.

    Args:
        ticker: TASE ticker symbol used to seed the random number generator.
        base: Starting price in ILS. Defaults to 100.0.
        days: Number of trading days to generate. Defaults to 60.

    Returns:
        List of OHLCV dicts with keys: date, open, high, low, close, volume.
    """
    random.seed(hash(ticker) % 9999)
    bars: list[dict] = []
    price = base
    for i in range(days):
        bar_date = date.today() - timedelta(days=days - 1 - i)
        change = random.gauss(0.001, 0.018)
        price = max(price * (1 + change), 1.0)
        bars.append({
            "date":   str(bar_date),
            "open":   round(price * 0.998, 2),
            "high":   round(price * 1.01, 2),
            "low":    round(price * 0.99, 2),
            "close":  round(price, 2),
            "volume": max(int(random.gauss(400000, 100000)), 10000),
        })
    return bars


def make_macro() -> MacroSnapshot:
    """Build a mock MacroSnapshot with representative Israeli macro data.

    Returns:
        A MacroSnapshot with BOI rate, CPI, FX rates, and index closes.
    """
    return MacroSnapshot(
        date=date.today(),
        boi_interest_rate=Decimal("4.50"),
        cpi_annual_pct=Decimal("3.2"),
        usd_ils_rate=Decimal("3.72"),
        eur_ils_rate=Decimal("4.05"),
        ta35_close=Decimal("2082"),
        ta125_close=Decimal("1645"),
    )


def fetch_fund_data() -> tuple[dict, str, int]:
    """Fetch live NAV data for all mutual fund positions via the funds connector.

    Attempts to import and call the optional funds connector. Returns empty
    results gracefully if the connector is unavailable or raises an error.

    Returns:
        A tuple of (funds_dict, funds_text, available_count) where:
        - funds_dict maps fund ID to NAV data dict (or None on failure).
        - funds_text is a formatted Hebrew string for the agent prompt.
        - available_count is the number of funds with successful NAV data.
    """
    try:
        from src.funds_connector import format_funds_for_agent, get_all_funds
        funds_dict = get_all_funds()
        funds_text = format_funds_for_agent(funds_dict)
        available = sum(1 for v in funds_dict.values() if v is not None)
        return funds_dict, funds_text, available
    except Exception as e:
        logger.warning("Could not load funds connector: %s", e)
        return {}, "Fund data unavailable.", 0


def main() -> None:
    """Run the full portfolio agent pipeline end-to-end and print a summary.

    Steps:
        1. Build mock portfolio (stocks + real fund positions).
        2. Fetch live NAV data from funder.co.il.
        3. Generate synthetic OHLCV data for the TA-125 universe + watchlist.
        4. Compute quantitative signals (RSI, MACD, momentum); tag watchlist tickers.
        5. Call Claude API for investment recommendations.
        6. Render HTML report, save locally, and attempt email delivery.
        6b. Mark-to-market open positions via price updater.
    """
    load_dotenv()

    print("\n" + "=" * 62)
    print("  🚀  Portfolio Agent — Demo Run")
    print("=" * 62)

    check_api_key()

    # Step 1 — portfolio
    print("\n[1/6] 🏦  Building portfolio (stocks + real fund positions)...")
    portfolio = make_portfolio()
    total = float(portfolio.total_value_ils)
    stocks_val = sum(
        float(h.market_value_ils)
        for h in portfolio.holdings
        if not h.ticker.startswith("FUND-")
    )
    funds_val = total - stocks_val
    funds_pnl = sum(
        float(h.unrealized_pnl_ils)
        for h in portfolio.holdings
        if h.ticker.startswith("FUND-")
    )

    print(f"      Total portfolio:  ₪{total:>12,.0f}")
    print(f"      ├─ Stocks:        ₪{stocks_val:>12,.0f}  ({stocks_val/total*100:.1f}%)")
    print(f"      └─ Mutual Funds:  ₪{funds_val:>12,.0f}  ({funds_val/total*100:.1f}%)")
    print()
    print("      STOCKS:")
    for h in portfolio.holdings:
        if not h.ticker.startswith("FUND-"):
            arrow = "📈" if h.unrealized_pnl_ils >= 0 else "📉"
            print(f"        {arrow} {h.ticker:<6} {h.company_name:<28} {h.unrealized_pnl_pct:+.1f}%")
    print()
    print("      MUTUAL FUNDS (real positions):")
    for h in portfolio.holdings:
        if h.ticker.startswith("FUND-"):
            arrow = "📈" if h.unrealized_pnl_ils > 0 else ("📉" if h.unrealized_pnl_ils < 0 else "➡️")
            print(f"        {arrow} {h.company_name:<42} ₪{float(h.market_value_ils):>10,.0f}  {h.unrealized_pnl_pct:+.2f}%")
    print(f"\n      Funds total P&L:  ₪{funds_pnl:>+,.0f}")

    # Step 2 — live NAV
    print("\n[2/6] 📊  Fetching live NAV data from funder.co.il...")
    funds_dict, funds_text, funds_available = fetch_fund_data()
    if funds_available > 0:
        print(f"      ✅ {funds_available}/{len(funds_dict)} funds — live NAV loaded")
        for fund_id, d in funds_dict.items():
            if d and d.get("nav"):
                chg = f"({d['change_1day']:+.2f}%)" if d.get("change_1day") is not None else ""
                print(f"      📊 {d.get('name','')[:45]:<45} ₪{d['nav']/10:.4f} {chg}")
    else:
        print("      ⚠️  Fund data unavailable")

    # Step 3 — market data
    print("\n[3/6] 📈  Generating market data for TA-125 universe + watchlist...")
    ohlcv_data = {t: make_ohlcv(t, base=50 + hash(t) % 400, days=60) for t in ALL_TICKERS}
    watchlist_ohlcv = {t: make_ohlcv(t, base=50 + hash(t) % 400, days=60) for t in WATCHLIST_TICKERS}
    ohlcv_data.update(watchlist_ohlcv)
    print(f"      Generated 60 days × {len(ALL_TICKERS)} universe tickers + {len(WATCHLIST_TICKERS)} watchlist")

    # Step 4 — quant signals
    print("\n[4/6] 🔢  Computing quant signals (RSI, MACD, momentum)...")
    sector_pes = {
        "Pharma": 18.5, "Technology": 25.0, "Banks": 10.0,
        "Materials": 14.0, "Telecom": 12.0, "Defense": 22.0,
    }
    engine = QuantEngine(sector_pe_medians=sector_pes)
    tickers_data = {t: {"bars": ohlcv_data[t], "info": None} for t in ALL_TICKERS}
    # Merge watchlist tickers into the same pipeline
    tickers_data.update({t: {"bars": ohlcv_data[t], "info": None} for t in WATCHLIST_TICKERS})
    signals = engine.compute_all(tickers_data)
    # Tag watchlist signals so Claude and the dashboard can distinguish them
    watchlist_set = set(WATCHLIST_TICKERS)
    for sig in signals:
        if sig.ticker in watchlist_set:
            sig.signal_summary = ["[WATCH]"] + sig.signal_summary
    bullish = [s for s in signals if (s.composite_score or 0) > 0.2]
    bearish = [s for s in signals if (s.composite_score or 0) < -0.2]
    print(f"      {len(bullish)} bullish signals, {len(bearish)} bearish signals")
    print(f"      Watchlist: {', '.join(WATCHLIST_TICKERS)} tagged [WATCH]")
    print(f"      Top 3: {', '.join(f'{s.ticker}({s.composite_score:+.2f})' for s in signals[:3])}")

    # Step 5 — Claude Opus 4
    print("\n[5/6] 🤖  Calling Claude Opus 4 for recommendations...")
    print("      (This takes 15-30 seconds — Claude is thinking...)")

    init_recommendations_table()
    perf_summary = get_performance_summary(days=30)
    performance_text = format_for_prompt(perf_summary)
    if perf_summary:
        print(f"      📊 Performance history: {perf_summary.total_recs} past recs, {perf_summary.success_rate}% success")
    else:
        print("      📊 No performance history yet — first run")

    macro = make_macro()
    index_perf = {"ta35": {"change_pct": -2.98}, "ta125": {"change_pct": -2.41}}
    news = [
        {
            "source": "Globes",
            "title": "Teva signs $2.3B biosimilar licensing deal with European partner",
            "body": "Teva Pharmaceutical announced a landmark licensing agreement for its biosimilar portfolio, expected to generate $400M in annual revenue by 2026.",
            "published_at": datetime.now().isoformat(),
            "tickers_mentioned": ["TEVA"],
        },
        {
            "source": "TheMarker",
            "title": "Bank of Israel signals possible rate cut in Q2 2025",
            "body": "BOI Governor Amir Yaron hinted at a potential interest rate reduction. Analysts expect a 25bps cut as early as April.",
            "published_at": datetime.now().isoformat(),
            "tickers_mentioned": ["LUMI", "POLI", "FIBI"],
        },
        {
            "source": "Calcalist",
            "title": "Elbit Systems wins $1.2B IDF contract for drone systems",
            "body": "Elbit Systems secured a major multi-year contract with the IDF for next-generation drone surveillance systems through 2027.",
            "published_at": datetime.now().isoformat(),
            "tickers_mentioned": ["ESLT"],
        },
        {
            "source": "TheMarker",
            "title": "TA-35 drops nearly 3% on global tech selloff",
            "body": "The Tel Aviv Stock Exchange's TA-35 fell sharply, dragged by global tech weakness and semiconductor pressure. Defense stocks showed resilience.",
            "published_at": datetime.now().isoformat(),
            "tickers_mentioned": [],
        },
    ]

    agent = PortfolioAgent()
    report, usage = agent.generate_report(
        portfolio, signals, macro, index_perf, news,
        run_type="morning",
        performance_text=performance_text,
        funds_text=funds_text,
    )
    print(f"      ✅ Done! {len(report.recommendations)} recommendations generated")
    print(f"         Tokens: {usage['prompt_tokens']:,} in / {usage['completion_tokens']:,} out")
    print(f"         Time: {usage['duration_s']}s | Model: {usage['model']}")

    # Save to DB
    saved_count = 0
    for rec in report.recommendations:
        held = {h.ticker: h.current_price for h in portfolio.holdings}
        entry_price = held.get(rec.ticker)
        record = RecommendationRecord(
            symbol=rec.ticker,
            action=rec.action.value,
            conviction=rec.conviction.value,
            thesis=rec.thesis,
            key_risk=rec.key_risk,
            price_entry=entry_price,
            price_target=Decimal(str(rec.price_target_ils)) if rec.price_target_ils else None,
            run_type="morning",
        )
        rec_id = save_recommendation(record)
        if rec_id:
            saved_count += 1
    if saved_count:
        print(f"      💾 Saved {saved_count} recommendations to DB")

    # Step 6 — render
    print("\n[6/6] 📧  Rendering report...")
    html = render_html_report(report, "demo-001")
    local_path = save_report_locally(html, report)
    send_email_report(html, report)

    # Step 6b — mark-to-market open positions
    print("\n[6b]  📡  Updating mark-to-market for open positions...")
    try:
        from src.price_updater import run_price_update
        mtm = run_price_update(verbose=False)
        if mtm.get("updated", 0) > 0:
            print(f"      ✅ Updated {mtm['updated']} open positions")
            if mtm.get("ta35"):
                print(f"      TA-35 benchmark: ₪{mtm['ta35']:.2f}")
        else:
            print("      ℹ️  No open approved positions to update yet")
    except (ImportError, RuntimeError) as e:
        print(f"      ⚠️  Price update skipped: {e}")

    # Summary
    print("\n" + "─" * 62)
    print("  RECOMMENDATIONS SUMMARY")
    print("─" * 62)
    for rec in report.recommendations:
        icons = {"BUY": "📈", "SELL": "📉", "HOLD": "➡️", "WATCH": "👁"}
        conv_icons = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "⚪"}
        print(
            f"\n  {icons.get(rec.action.value,'?')} {rec.action.value:<5} {rec.ticker:<22} "
            f"{conv_icons.get(rec.conviction.value,'')} {rec.conviction.value}"
        )
        print(f"     {rec.thesis[:110]}...")
        if rec.suggested_position_pct > 0:
            print(f"     Position: {rec.suggested_position_pct}%", end="")
            if rec.price_target_ils:
                print(f"  |  Target: ₪{rec.price_target_ils:.2f}", end="")
            print()

    if report.portfolio_risk_flags:
        print(f"\n  ⚠️  Risk flags:")
        for f in report.portfolio_risk_flags:
            print(f"     • {f}")

    print("\n" + "=" * 62)
    print("  ✅  DEMO COMPLETE!")
    print(f"\n  📄 Open your report:")
    print(f"     {local_path}")
    print("=" * 62 + "\n")


if __name__ == "__main__":
    main()
