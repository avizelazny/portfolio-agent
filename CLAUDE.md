# CLAUDE.md — Portfolio Agent

> This file is the single source of truth for Claude Code working on this project.
> Read it fully before every session. Update it when you learn something new.

---

## 🗺️ Project Overview

**What this is:** An autonomous portfolio monitoring agent for the Israeli capital market (TASE).
It fetches market data, runs quantitative analysis, and generates HTML reports — delivered via email on a schedule.

**Owner:** Avi — solo developer, learning-first approach. No unnecessary shortcuts.
**Goal:** A real system solving a real need, built with full understanding of every layer.

---

## 🏗️ Architecture

```
[EventBridge Scheduler]
        │
        ▼
[ECS Task — agent_core.py]
        │
        ├──► [connectors/] ──► TASE / Maya API  (Phase 2: Bank Discount)
        │
        ├──► [quant_engine.py] ──► quantitative analysis
        │
        ├──► [report_renderer.py] ──► HTML report → /reports/
        │
        └──► [SES] ──► email delivery

[db/] ──► PostgreSQL / pgvector  (Phase 2)
[infra/] ──► Terraform (AWS ECS, EventBridge, SES)
```

**Entry points:**
- `agent_core.py` — main agent loop
- `dashboard.py` — local dev dashboard (Flask, localhost:5000)
- `demo_run.py` — quick smoke test / demo
- `approve.py` — CLI tool for approving/rejecting recommendations

**Data models:** `src/models/market.py`, `src/models/report.py`
**Config:** `src/utils/config.py` + `.env` (see `.env.example`)
**Portfolio config:** `portfolio.yaml` — holdings, mandate, pending_orders

---

## 📦 Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.12 |
| Infra | AWS ECS + EventBridge + SES via Terraform |
| Data sources | yfinance (stocks), funder.co.il (fund NAV), Bank of Israel SDMX-ML (macro), Maya API |
| Database | SQLite (local), PostgreSQL + pgvector (Phase 2) |
| Packaging | Docker (`docker-compose.yml`) |
| Formatting | Black + PEP8 |
| AI core | Claude Opus 4 |

---

## ✅ Coding Standards

These are non-negotiable. Apply them to every file you touch.

### Python Style
- **Type hints always** — every function signature, every return type
- **Docstrings on every function** — Google style, one-liner minimum, multi-line for complex logic
- **Black formatting** — line length 88, enforce on every file
- **PEP8 strict** — no exceptions
- **Readable > clever** — if you have to choose, choose readable
- **Imports order:** stdlib → third-party → local (isort-compatible)

```python
# ✅ Correct
def fetch_security_data(ticker: str, days: int = 30) -> pd.DataFrame:
    """Fetch historical OHLCV data for a given ticker from the TASE API.

    Args:
        ticker: TASE security identifier (e.g., "1082").
        days: Number of trading days to fetch. Defaults to 30.

    Returns:
        DataFrame with columns: date, open, high, low, close, volume.

    Raises:
        TASEAPIError: If the ticker is invalid or the API is unreachable.
    """
    ...

# ❌ Wrong
def fetch(t, d=30):
    # gets data
    ...
```

### Error Handling
- Raise specific exceptions, not bare `Exception`
- Log errors with context before re-raising
- Never silently swallow exceptions

### Comments
- English or Hebrew — either is fine, stay consistent within a file
- Comment the *why*, not the *what*

---

## 🧠 How to Work With Me (Avi)

### Architectural Decisions — ALWAYS Ask First
When a task involves an architectural choice (new module, design pattern, data model, infra change), **stop and present options before writing code**. Format:

```
🏗️ Architectural Decision Required

Context: [what we're trying to do]

Option A: [name]
  Pros: ...
  Cons: ...

Option B: [name]
  Pros: ...
  Cons: ...

My recommendation: [Option X] because [reason].
Which do you prefer?
```

Do **not** make unilateral architectural decisions. I decide.

### Planning Mode — Before Complex Tasks
For any task touching more than one file or introducing new logic:
1. Write a short plan (3–5 bullet points)
2. Wait for approval
3. Then code

### Verification — Before Closing a Task
Before saying a task is done:
1. Show which files were changed and why
2. Confirm the code runs (or explain how to verify)
3. Note any edge cases or follow-up items

### Session Closing Prompt
At the end of every dev session, always ask:
> "Before we close — anything new to add to CLAUDE.md? (lessons learned, backlog changes, architectural decisions)"

---

## 📚 Lessons Learned

> This section grows over time. Every time I correct Claude, the fix goes here so it never repeats.

### TASE / Data

#### 2026-03-13 — TASE index tickers use plain `.TA` suffix, no caret
What happened: yfinance fetch for TA-35 returned 404 errors.
Root cause: Used `^TA35.TA` (US-style caret prefix) instead of `TA35.TA`.
Fix: Remove the caret. US indices use `^GSPC` style; TASE indices do not.
Rule going forward: TASE tickers are always `TICKER.TA` with no caret. E.g. `TA35.TA`, `TA125.TA`, `TEVA.TA`.
**Repeat offender:** Found caret violations again in `tase.py` (`^TA35`, `^TA90`, `^TA125`) and `macro_connector.py` (`^TA125.TA`) during the 2026-03-29 review. When adding any new yfinance call for a TASE index, grep for `\^TA` before committing.

#### 2026-03-13 — funder.co.il rate-limits rapid NAV fetches
What happened: `price_updater.py` timed out when fetching multiple fund NAVs in quick succession.
Root cause: funder.co.il silently rate-limits scrapers that make back-to-back requests.
Fix: Add `time.sleep()` delays between fund NAV fetches. Skip mark-to-market for funds in `price_updater.py` — fund NAV only updates once daily after market close anyway.
Rule going forward: Fund NAV is a once-daily post-close value. Fetch it once in the evening run; don't re-fetch mid-day.

#### 2026-03-10 — Globes is more reliable than TASE direct for security data
What happened: Navigating to TASE's own market data interface didn't yield extractable text.
Fix: Use `globes.co.il/portal/instrument.aspx?instrumentid=[TASE_ID]` with the numeric TASE security ID. Returns structured, parseable data reliably.
Rule going forward: For TASE-listed securities, Globes instrument page is the primary data source. TASE direct is a fallback only.

#### 2026-03-23 — TA-35 widget: compare prev-session close, not open vs close
What happened: TA-35 intraday delta widget was inflating the move by ~2%.
Root cause: Code compared today's open vs current price. On days after Sunday gaps (TASE is closed Fri afternoon–Sun), the open vs close difference included the gap, not just intraday movement.
Fix: Always compare current price vs previous session's close.
Rule going forward: Any "today's change" calculation must use prev_close as the baseline, never today's open.

### Python / Architecture

#### 2026-03-13 — Multiline `python -c "..."` doesn't work in Windows CMD
What happened: One-off DB migration scripts passed as `-c` arguments broke in CMD — each line was interpreted as a separate shell command.
Root cause: Windows CMD doesn't support multiline string arguments to executables.
Fix: Always deliver one-off scripts as `.py` files, never as inline `-c` commands.
Rule going forward: Any time Claude wants to run a multi-line Python snippet, write it as a `.py` file. Never use `python -c "..."` for anything more than a one-liner.

#### 2026-03-13 — Use forward slashes in Python path strings on Windows
What happened: Backslash sequences in path string literals caused `SyntaxWarning` (e.g. `\m`, `\p`, `\d` interpreted as escape sequences).
Fix: Use forward slashes in Python string literals — `"C:/portfolio-demo/..."`. Python on Windows handles them correctly.
Rule going forward: Always write file paths in Python with forward slashes. Never use raw strings or double-backslash unless there's a specific reason.

#### 2026-03-13 — DB schema migrations: use `ADD COLUMN IF NOT EXISTS`
What happened: Running a schema update on an existing DB raised errors because columns already existed.
Fix: Use `ALTER TABLE ... ADD COLUMN IF NOT EXISTS ...` for safe iterative schema changes.
Rule going forward: All `ALTER TABLE` migration scripts must use `IF NOT EXISTS`. Never assume a fresh DB.

#### 2026-03-24 — Polling loops must never re-initialize UI state on every cycle
What happened: Batch selector dropdown snapped back to the latest batch every 5 seconds.
Root cause: `fetchState()` called `populateBatchSelector()` on every poll cycle, which reset `select.value` to the most recent batch and overwrote the user's selection.
Fix: Add a guard at the top of `populateBatchSelector()`: if options already exist, return immediately. Populate once on first load, never again. The `selectedBatch` module-level variable then persists correctly across all poll cycles.
Rule going forward: Any UI state set by the user (selected tab, selected batch, open panel) must be stored in a module-level JS variable. Polling loops read that variable — they never reset it. One-time init functions must guard against re-running.

#### 2026-03-24 — JS state variables must be module-level, not DOM-derived
What happened: `renderRecs()` tried to read `selectedBatch` from the DOM (`select.value`) on every call. When the polling loop reset the DOM, the variable appeared to persist but the rendered output changed unexpectedly.
Rule going forward: User-driven UI state belongs in a JS module-level variable, not in the DOM. The DOM reflects state — it does not store it. Read from the variable, write to the DOM.

#### 2026-03-25 — DB path must be anchored to `__file__`, not CWD-relative
What happened: `approve.py` silently wrote to a stale `portfolio.db` at project root instead of `data/portfolio.db`.
Root cause: `Path("data/portfolio.db")` resolves relative to whatever directory the script is launched from.
Fix: Anchor all DB paths to `__file__`: `Path(__file__).resolve().parent.parent.parent / "data" / "portfolio.db"`
Rule going forward: Never use relative paths for DB files. Always anchor to `__file__`.

#### 2026-03-25 — SQLite returns datetime columns as plain strings
What happened: `.replace(tzinfo=None)` called on a string — crash in `show_open()`.
Root cause: `sqlite3` returns TEXT columns as Python strings, not `datetime` objects.
Fix: Always parse explicitly: `datetime.strptime(value, "%Y-%m-%d %H:%M:%S")`
Rule going forward: Never assume SQLite returns typed values. Strings need `strptime`, numbers need explicit cast.

#### 2026-03-25 — CLI tools must print their DB path on startup
What happened: `approve.py` completed silently but wrote nothing — no way to tell it had targeted the wrong DB.
Rule going forward: Any script that reads or writes the DB prints `[DB] <path>` on startup. Silent success is indistinguishable from silent failure.

#### 2026-03-29 — openpyxl workbooks must be closed in try/finally on Windows
What happened: `discount_parser.py`, `transaction_parser.py` held open Excel file handles after parsing.
Root cause: openpyxl `load_workbook()` opens a file handle that is not released on exception. Windows locks the file, blocking subsequent reads or writes.
Fix: Wrap the entire parse body in `try/finally: wb.close()`.
Rule going forward: Every `openpyxl.load_workbook()` call must be paired with `wb.close()` in a `finally` block. No exceptions.

#### 2026-03-29 — `datetime.utcnow()` is deprecated in Python 3.12
What happened: Multiple files used `datetime.utcnow()`, which emits `DeprecationWarning` in Python 3.12 and will be removed.
Fix: Replace with `datetime.now(timezone.utc).replace(tzinfo=None)` for a naive UTC datetime (SQLite-compatible), or `datetime.now(timezone.utc)` when a tz-aware object is needed.
Rule going forward: Never use `datetime.utcnow()`. Always import `timezone` from `datetime` and use `datetime.now(timezone.utc)`.

#### 2026-03-29 — sqlite3.Row → dict: use `dict(r)`, never `dict(zip(col_names, r))`
What happened: `trade_matcher.py` built dicts with `dict(zip(["id", "symbol", ...], r))`, hardcoding column order.
Root cause: If the SELECT column list ever changes order, the zip produces silently wrong mappings.
Fix: Set `conn.row_factory = sqlite3.Row` (already done in `get_connection()`) and convert with `dict(r)`. sqlite3.Row supports key-based access, so `dict(r)` is always correct regardless of query order.
Rule going forward: Never use `dict(zip(col_list, row))`. Always use `dict(r)` when `row_factory = sqlite3.Row` is set.

#### 2026-03-29 — f-string `:+` format spec crashes on string fallback values
What happened: `agent_core.py` formatted index performance as `{change_pct:+}%` with a fallback of `'N/A'`. When data was unavailable the fallback string hit the `:+` format spec, raising `ValueError: Sign not allowed in string format specification`.
Fix: Pre-extract the value, then conditionally format: `f"{v:+.2f}" if v is not None else "N/A"`.
Rule going forward: Never apply numeric format specs (`:+`, `:.2f`, `:,`) to variables that might be strings. Always pre-extract and branch.

#### 2026-03-29 — File uploads must use `secure_filename()` to prevent path traversal
What happened: `dashboard.py` `api_upload` saved user-supplied filenames directly to disk without sanitisation.
Root cause: A filename like `../../etc/passwd` or `../app.py` would write outside the upload directory.
Fix: `from werkzeug.utils import secure_filename; safe_name = secure_filename(f.filename)`.
Rule going forward: Any route that accepts a file upload must call `secure_filename()` before constructing the save path. Never trust `f.filename` directly.

#### 2026-03-29 — SQLite datetime bug propagates: check ALL callers of `created_at`
What happened: The "SQLite returns strings" lesson (2026-03-25) was applied to `approve.py` but the same pattern recurred in `price_updater.py` (`.replace(tzinfo=None)` on a string) and `snapshot_runner.py` (`.tzinfo` attribute access on a string).
Rule going forward: Whenever new code reads a datetime column from SQLite, immediately wrap it in `strptime`. Search for `.tzinfo`, `.replace(tzinfo`, `.strftime` on variables sourced from DB rows — each is a latent crash.

#### 2026-03-29 — All YAML and DB paths must be `__file__`-anchored — repeat offender
What happened: CWD-relative `"portfolio.yaml"` calls found in `approve.py` (orders command), `dashboard.py` (background run), `demo_run.py` (load_universes), and `agent_core.py` (`load_pending_orders`, `load_mandate`).
Rule going forward: Any time a new function opens `portfolio.yaml` or any project file, the first line of the path construction must be `Path(__file__).resolve().parent...`. Never use bare string literals for project-relative paths.

#### 2026-03-25 — Investment mandate is config, not hardcode
What happened: Decided not to hardcode the 10% nominal target in `agent_core.py` or the scorer.
Decision: All mandate values live in `portfolio.yaml` under the `mandate:` block.
The scorer, SYSTEM_PROMPT, and hit-rate panel all read `hurdle_rate_pct` from `portfolio.yaml`.
To switch to conservative mode (CPI+5% = 8% nominal): set `hurdle_rate_pct: 8.0` — nothing else changes.
Rule going forward: Any tunable investment parameter (target return, CPI assumption, benchmark type) belongs in `portfolio.yaml`, never hardcoded.

### Infra / AWS

*(empty — add as we go)*

---

## 🚧 Phase Map

| Phase | Status | Scope |
|---|---|---|
| Phase 1 | ✅ Complete | Core agent, TASE data, quant engine, HTML reports, dashboard, approve.py |
| Phase A+B | ✅ Complete | Feedback system — recommendation scoring, rejection history, hit-rate tracking |
| Phase Bug Review | ✅ Complete (2026-03-29) | Full codebase sweep — 14 files fixed, 18 bugs resolved |
| Phase 2 | 🔜 Planned | AWS ECS + EventBridge + SES deployment, PostgreSQL + pgvector, Docker |

**Current focus:** Hit-rate dashboard panel (#4). Unacted tracking (#3). REDUCE action (#13).

---

## 📋 Backlog

| # | Area | Item |
|---|---|---|
| 1 | Measurement | `benchmark_return_7d/30d` columns in recommendations table vs TA-35 |
| 2 | Measurement | `recommendation_scorer.py` — nightly job scoring open recs vs benchmark |
| 3 | Measurement | `unacted_tracking` — track rejected/ignored recs in background |
| 4 | Measurement | Hit-rate dashboard panel — rolling % last 10/30/50 recs, split by acted/unacted |
| 5 | Measurement | Rejection history with reasons injected as XML into Claude context (`_build_context`) |
| 6 | Data | Earnings calendar from Maya API |
| 7 | Data | Institutional ownership changes via Maya |
| 8 | Data | USD/ILS 30-day momentum trend |
| 9 | Data | Sector rotation signals within TA-35 |
| 10 | Data | Short interest data from TASE |
| 11 | Data | Dividend calendar / ex-div dates |
| 12 | Infra/UX | REDUCE action mapping in approve.py |
| 13 | Infra/UX | 6-step evaluator widget in dashboard |
| 14 | Infra/UX | AWS ECS + EventBridge + SES deployment |
| 15 | Infra/UX | Live stock prices in Claude context for entry guidance |
| 16 | Agent | Apply updated SYSTEM_PROMPT — 11% nominal target, favour conviction, no unnecessary HOLDs (file: `updated_system_prompt.py`) |
| 17 | Agent | ✅ DONE (prior session) — build_system_prompt() reads hurdle_rate_pct from portfolio.yaml at runtime. Conviction-first, no unnecessary HOLDs, underperformance framed as risk. updated_system_prompt.py was never needed — mandate-driven prompt was built directly into agent_core.py. |
| 18 | Infra/UX | `price_limit` field on recommendation record — store the limit order entry price the user sets, so expired limits leave a trace in the DB. Currently this lives only in portfolio.yaml pending_orders and disappears when the order is cancelled. |

---

## 🔄 Weekly Operating Routine

**Sunday evening:**
1. Export 3 files from Bank Discount:
   - התיק שלי → Excel
   - הוראות וביצועים → ביצועים היסטוריים → Excel
   - הוראות וביצועים → הוראות → Excel
2. Run `approve.py supersede-all` (supersedes any open recs from prior week)
3. Open `localhost:5000`, upload all 3 files, click UPLOAD & RUN
4. Wait ~80 seconds, review 10–12 recommendations
5. For each: run AI Analysis, approve/reject with reason

**Monday:** Execute approved trades, log prices with `approve.py yes [id] [price] [qty]`

**Monthly:** `approve.py perf`

**Every 6 months:** Update TA-125 benchmark in `portfolio.yaml`

**Standing rules:**
- Never trade on Claude's recommendation alone
- Always reject with a stated reason
- Max 2 trades per week
- Always use limit orders for stocks, never market
- Sell first, then buy
- Close positions via `approve.py close [id]`

---

## 🚫 Off-Limits Without Discussion

- Do not modify `infra/*.tf` files without explicit instruction
- Do not change data models in `src/models/` without checking impact on `quant_engine.py` and `report_renderer.py`
- Do not add dependencies to `requirements.txt` without proposing them first
- Do not touch `db/` — Phase 2 only

---

## 🔄 How to Update This File

When you learn something new about this project — a bug pattern, a data quirk, a naming convention — add it to **Lessons Learned** immediately. Use this format:

```
#### YYYY-MM-DD — Short title
What happened: ...
Root cause: ...
Fix: ...
Rule going forward: ...
```
