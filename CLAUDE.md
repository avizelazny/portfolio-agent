# CLAUDE.md — Portfolio Agent Codebase Guide

This file is the primary reference for AI assistants (Claude and others) working on this repository. It covers architecture, conventions, data flows, and development workflows.

---

## Project Overview

`portfolio-agent` is an MVP autonomous portfolio management system for Israeli equities. It uses **Claude Opus 4** (`claude-opus-4-6`) as a senior portfolio manager persona to analyze TASE (Tel Aviv Stock Exchange) holdings, compute quantitative signals, and generate investment recommendations.

**Key characteristics:**
- Designed to run twice daily: **9:30 AM IST** (market open) and **4:00 PM IST** (market close), Monday–Friday
- **Human-in-the-loop**: all recommendations are stored and require human approval before any trade execution
- Performance tracked over time and fed back into Claude's context to improve future recommendations
- Production target: AWS EventBridge → ECS Fargate; local dev uses Docker Compose

---

## Directory Structure

```
portfolio-agent/
├── dashboard.py              # Flask web UI (entry point #1) — manual triggering & monitoring
├── demo_run.py               # Headless CLI demo pipeline (entry point #2) — integration test
├── requirements.txt          # Python 3.12 dependencies
├── docker-compose.yml        # Local dev infra: PostgreSQL, Redis, MinIO, MailHog
├── .env.example              # All required environment variables with placeholder values
├── README.md                 # User-facing documentation
├── LICENSE                   # MIT license
└── src/
    ├── agent_core.py         # Claude API integration — PortfolioAgent class
    ├── quant_engine.py       # Technical signal computation — QuantEngine class
    ├── report_renderer.py    # HTML report generation — Jinja2 templating
    ├── models/
    │   ├── market.py         # Holding, PortfolioSnapshot, MacroSnapshot, QuantSignals
    │   ├── report.py         # StockRecommendation, RecommendationReport, Action, Conviction
    │   └── recommendation.py # RecommendationRecord, ApprovalUpdate, OutcomeUpdate, PerformanceSummary
    ├── db/
    │   └── recommendations_db.py  # PostgreSQL schema, CRUD, lifecycle tracking, performance stats
    ├── utils/
    │   └── config.py         # Config dataclass — loads from .env / environment
    └── connectors/
        └── __init__.py       # Placeholder for future TASE DataCloud / Bank Discount connectors
```

---

## Architecture

### Pipeline (end-to-end flow)

```
Entry point (dashboard.py or demo_run.py)
  │
  ├─► QuantEngine.compute_all(universe, bars)
  │     └─► per ticker: RSI, MACD, momentum, volume, P/E, 52w → QuantSignals (composite score)
  │
  ├─► PortfolioAgent.generate_report(portfolio, signals, macro, news)
  │     ├─► _build_context()        — assembles XML-like structured prompt
  │     ├─► Claude Opus 4 API call  — generates JSON recommendations
  │     └─► _parse()                — extracts JSON → Pydantic RecommendationReport
  │
  ├─► render_html_report()          — Jinja2 → styled HTML
  ├─► save_report_locally()         — writes to reports/YYYY-MM-DD/{type}_{HHMM}.html
  ├─► save_recommendation()         — persists to PostgreSQL
  └─► send_email_report()           — SES/SMTP (optional, graceful fallback)
```

### Infrastructure (local dev via Docker Compose)

| Service    | Port(s)       | Purpose                              |
|------------|---------------|--------------------------------------|
| PostgreSQL | 5432          | Recommendations DB + pgvector ready  |
| Redis      | 6379          | Caching (future use)                 |
| MinIO      | 9000 / 9001   | S3-compatible report storage         |
| MailHog    | 1025 / 8025   | Local SMTP capture / web UI          |

---

## Development Setup

### Prerequisites
- Python 3.12
- Docker + Docker Compose

### Steps

```bash
# 1. Copy environment config
cp .env.example .env
# Edit .env — at minimum set ANTHROPIC_API_KEY

# 2. Start local infrastructure
docker compose up -d

# 3. Install Python dependencies
pip install -r requirements.txt

# 4. Run the Flask dashboard
python dashboard.py
# → http://localhost:5000

# 5. Or run the headless demo (no real APIs needed)
python demo_run.py
```

### Running with real data vs mock data
- `demo_run.py` generates fully synthetic portfolio and OHLCV bars — **no external APIs required** (except `ANTHROPIC_API_KEY` for Claude)
- `dashboard.py` wires up the same pipeline and can be extended with real connectors
- All other API keys (`TASE_API_KEY`, `BANK_DISCOUNT_API_KEY`) are for Phase 2 connectors not yet implemented

---

## Environment Variables

All variables are defined in `.env.example`. Loaded by `src/utils/config.py` via `python-dotenv`.

| Variable                  | Required | Description                                           |
|---------------------------|----------|-------------------------------------------------------|
| `ANTHROPIC_API_KEY`       | Yes      | Claude API key (`sk-ant-...`)                         |
| `ENVIRONMENT`             | No       | `local` / `production` (default: `local`)             |
| `DB_HOST`                 | No       | PostgreSQL host (default: `localhost`)                |
| `DB_PORT`                 | No       | PostgreSQL port (default: `5432`)                     |
| `DB_NAME`                 | No       | Database name (default: `portfolio_agent`)            |
| `DB_USER`                 | No       | DB user (default: `agent_admin`)                      |
| `DB_PASSWORD`             | No       | DB password (default: `localdev123`)                  |
| `REDIS_HOST`              | No       | Redis host (default: `localhost`)                     |
| `REDIS_PORT`              | No       | Redis port (default: `6379`)                          |
| `BANK_DISCOUNT_API_KEY`   | No       | Bank Discount portfolio API (Phase 2)                 |
| `BANK_DISCOUNT_API_URL`   | No       | Bank Discount API URL (Phase 2)                       |
| `TASE_API_KEY`            | No       | TASE DataCloud API key (Phase 2)                      |
| `TASE_API_URL`            | No       | TASE DataCloud URL (Phase 2)                          |
| `VOYAGE_API_KEY`          | No       | Voyage AI embeddings key (Phase 2)                    |
| `S3_ENDPOINT_URL`         | No       | S3/MinIO endpoint (default: `http://localhost:9000`)  |
| `S3_ACCESS_KEY`           | No       | S3/MinIO access key (default: `minioadmin`)           |
| `S3_SECRET_KEY`           | No       | S3/MinIO secret key (default: `minioadmin123`)        |
| `REPORTS_BUCKET`          | No       | S3 bucket for HTML reports                            |
| `EMAIL_HOST`              | No       | SMTP host (default: `localhost`)                      |
| `EMAIL_PORT`              | No       | SMTP port (default: `1025`)                           |
| `REPORT_RECIPIENT_EMAIL`  | No       | Who receives the daily report email                   |
| `SES_SENDER_EMAIL`        | No       | Sender address for SES emails                         |

**Never hardcode any of these values in source code.** Always use `Config.from_env()`.

---

## Key Modules

### `src/agent_core.py` — Claude Integration

Central class: `PortfolioAgent`

```python
CLAUDE_MODEL = "claude-opus-4-6"   # always use this constant, do not hardcode the string elsewhere
```

**System prompt persona:** Senior Israeli equities portfolio manager with 20 years TASE experience. Deeply familiar with TA-35, TA-125, BOI policy, CPI, and USD/ILS dynamics.

**Key methods:**
- `_build_context(portfolio, signals, macro, news)` — assembles the full prompt as XML-like blocks (`<portfolio>`, `<quant_signals>`, `<macro>`, `<news>`, `<past_performance>`). **Edit here** to change what context Claude receives.
- `generate_report(...)` → `RecommendationReport` — makes the API call and returns typed output
- `_parse(response_text)` — strips markdown fences, extracts JSON, maps to Pydantic. Handles malformed responses gracefully.

When modifying the system prompt or context structure, only edit `agent_core.py`. Do not scatter prompt logic across other files.

---

### `src/quant_engine.py` — Technical Signal Computation

Central class: `QuantEngine`

**Signal weights (composite score: -1.0 bearish → +1.0 bullish):**

| Signal             | Weight | Method                             |
|--------------------|--------|------------------------------------|
| RSI-14             | 20%    | Oversold (<30) / overbought (>70)  |
| MACD (12/26/9)     | 20%    | Histogram sign + divergence        |
| Momentum-20d       | 20%    | Price return over 20 trading days  |
| Volume z-score     | 15%    | Anomaly detection (±2σ)            |
| P/E vs sector      | 15%    | Relative valuation vs median P/E   |
| 52-week position   | 10%    | Price position within 52w range    |

**Key methods:**
- `compute_signals(ticker, bars) → QuantSignals`
- `compute_all(universe_tickers, bars_dict) → dict[str, QuantSignals]`

Input `bars` is expected as a list of OHLCV dicts with keys: `open`, `high`, `low`, `close`, `volume`, `pe_ratio`.

---

### `src/models/` — Pydantic Data Models

All models use **Pydantic v2**. Use `model_validator` and `field_validator` for custom validation.

**market.py:**
- `Holding` — ticker, quantity, cost_basis, current_price, unrealized_pnl
- `PortfolioSnapshot` — list of holdings, total_value, cash, daily_pnl
- `MacroSnapshot` — boi_rate, cpi, usd_ils, ta35_close, ta125_close
- `QuantSignals` — all 6 raw signals + composite_score + flags (is_oversold, is_overbought, high_volume_anomaly)

**report.py:**
- `Action` (enum) — `BUY`, `SELL`, `HOLD`, `WATCH`
- `Conviction` (enum) — `HIGH`, `MEDIUM`, `LOW`
- `StockRecommendation` — ticker, action, conviction, thesis, key_risk, target_price, stop_loss
- `RecommendationReport` — list of recommendations + helpers: `.buys()`, `.sells()`, `.holds()`, `.high_conviction()`

**recommendation.py:**
- `RecommendationRecord` — persisted recommendation with entry prices and TA-35 snapshot at time of recommendation
- `ApprovalUpdate` — captures human decision: approved bool, actual_price, quantity, note
- `OutcomeUpdate` — mark-to-market price update
- `PerformanceSummary` — 30-day stats: win_rate, alpha_vs_ta35, breakdown by conviction level

---

### `src/db/recommendations_db.py` — PostgreSQL Persistence

**Always call `init_recommendations_table()` once before any DB operations** (safe, uses `IF NOT EXISTS`).

**Recommendation lifecycle:**
```
save_recommendation()           # new recommendation persisted
  → update_approval()           # human approves or rejects
  → mark_to_market()            # periodic price updates
  → close_recommendation()      # position closed, exit price recorded
```

**Key functions:**
- `get_recommendations(start_date, end_date)` — query by date range
- `get_performance_summary()` — 30-day win rate, alpha, conviction breakdown
- `format_for_prompt()` — **returns past performance string formatted for insertion into Claude context** — this is the feedback loop

---

### `src/utils/config.py` — Configuration

```python
config = Config.from_env()   # use this everywhere, do not instantiate Config() directly
```

The `Config` dataclass reads all values from environment variables with sensible local defaults. Sensitive fields (`api_key`, passwords) have no defaults and will raise if missing in production.

---

### `src/report_renderer.py` — HTML Report Generation

- `render_html_report(report: RecommendationReport) → str` — Jinja2 → dark-themed HTML with color-coded action badges
- `save_report_locally(html, run_type) → Path` — saves to `reports/YYYY-MM-DD/{run_type}_{HHMM}.html`
- `send_email_report(html, subject)` — AWS SES integration, fails gracefully (logs warning, does not raise)

Reports directory (`reports/`) is git-ignored.

---

## Coding Conventions

- **Language:** Python 3.12 with full type annotations on all function signatures
- **Validation:** Pydantic v2 for all data structures — no raw dicts passed between modules
- **Logging:** Use `structlog` throughout — not `print()` or `logging.info()`
- **Retries:** Use `tenacity` decorators for all external API calls (Claude, TASE, Bank Discount)
- **Configuration:** Always `Config.from_env()` — never hardcode secrets or URLs
- **Financials:** Use Israeli Shekel (ILS / ₪) for all local asset prices and P&L
- **Prompt changes:** All Claude prompt logic lives in `src/agent_core.py` — keep it consolidated

---

## Testing

There is currently **no formal test suite**. The project is MVP stage.

**To validate the end-to-end pipeline:**
```bash
python demo_run.py        # runs full pipeline with synthetic data, saves HTML report
```

**To validate via UI:**
```bash
python dashboard.py       # Flask dashboard at http://localhost:5000
# Click "Run Demo" to trigger a pipeline run and stream logs
```

**Required for any run:** `ANTHROPIC_API_KEY` must be valid. All other external APIs use mock/synthetic data in demo mode.

**To add tests in future:** create a `tests/` directory and use `pytest`. Pattern: `tests/test_{module}.py`.

---

## What Is NOT Yet Implemented (Phase 2)

The following are planned but absent from the codebase. Do not attempt to call these unless implementing them:

- `src/connectors/` — Real TASE DataCloud API connector
- `src/connectors/` — Bank Discount portfolio API connector
- News scraping + Voyage AI vector embeddings for semantic retrieval
- `infra/` — Terraform / AWS CDK infrastructure code
- GitHub Actions CI/CD workflows
- Pre-commit hooks (black, ruff, mypy)
- Unit tests

---

## Git Conventions

- **Branch naming:** `feature/*`, `fix/*`, `chore/*`
- **Commit messages:** imperative mood, present tense (e.g., `Add CLAUDE.md with codebase guide`)
- No CI gates currently — manual code review only
- `.env` is git-ignored — never commit secrets
