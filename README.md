# XAUUSD Signal Intelligence System

## Project Overview

XAUUSD Signal Intelligence System is a stateless, cron-driven trading automation platform for gold (XAUUSD). It ingests live market and macro data, applies Smart Money Concepts (SMC) and macro-fundamental filters, scores opportunities through a multi-engine confluence stack, and publishes threaded Telegram alerts.

Version 2 upgrades (see docs/knowledge_base.md for the evidence base):

- Order-type aware execution: breakout signals are STOP orders, zone retests are LIMIT orders, each with correct activation logic.
- Wider, structure-aware stops: 1.5x ATR with a hard minimum distance and round-number clearance, ending the noise stop-out problem.
- Full trade lifecycle: TP1 partial, breakeven runner, pending-order expiry, and a time stop for stagnant trades.
- Confluence engines: session killzones, volatility regime, RSI/EMA momentum with exhaustion vetoes, multi-timeframe bias, barbwire/climax market-state vetoes, and fib OTE confluence.
- Self-learning: per-strategy weights driven by rolling expectancy (R), not win rate.
- Risk governor: daily signal caps, stop-loss cooldowns, losing-streak halts, daily -3R circuit breaker, +4R profit lock, and news blackouts.
- Trendline gate: counter-trend signals are blocked until the trendline through the last two swing pivots is broken by a body close.
- Evidence loop: every trade records its max favorable/adverse excursion in R; scripts/calibrate_from_history.py turns that into concrete tuning recommendations.
- Four strategy families: pin bars (two grades), engulfing-at-zone, Brooks H2/L2 with-trend pullbacks, inside-bar traps — plus SMC zone retests and Quasimodo sweep-reversal limits.
- Runner management: breakeven after TP1, structure-flip exit, time stop — winners protected three ways.
- Conviction-tiered sizing: Tier 1 signals size at 2% risk, Tier 2 at 1%.
- Daily floor-trader pivots (BabyPips formulas) and London-to-NY continuation as confluence inputs.
- Telegram reasoning is a full professional trade plan: tier, context, location, liquidity story, trigger, evidence, numbers with reasons, and pre-committed if-then management.
- Web dashboard: performance analytics (equity curve in R, per-strategy expectancy, MFE/MAE), risk governor console with manual kill switch and news blackout editor, live market-state page.
- Hosting: runs free and permanently on GitHub Actions (docs/hosting.md) - no cPanel required. Default signal timeframe is now M5.

The project remains compatible with any Linux host with cron.

## Purpose of the Platform

The platform provides disciplined, repeatable signal generation for XAUUSD with clear lifecycle management. It is designed to replace ad-hoc discretionary workflows with a deterministic pulse architecture that can be audited, tested, and safely deployed.

## Problem It Solves

- Reduces inconsistent manual analysis and delayed decision-making.
- Integrates technical and macro context into a unified confluence score.
- Eliminates ambiguous alerting by enforcing structured, threaded Telegram updates.
- Supports resilient shared-hosting deployment using SQLite WAL, lock control, and recovery scripts.

## How the Platform Works

1. Cron triggers one pulse per minute.
2. src/bot_runner.py acquires lock protection to avoid overlapping runs.
3. Orchestrator fetches live market data (Yahoo Finance) and macro inputs (FRED and related sources).
4. Data is validated and stored in SQLite (WAL mode).
5. SMC and macro engines update state and evaluate setups.
6. Scoring engine classifies setups (rejected/watchlist/actionable).
7. Actionable setups are converted into signals and dispatched to Telegram.
8. Lifecycle manager monitors active signals and posts TP/SL updates in the same message thread.

## Key Features

- Stateless pulse architecture for reliability and low resource usage.
- SQLite WAL persistence with index optimization and lock-friendly access.
- Structured Telegram alerting with threaded follow-ups.
- Dynamic lot-size guidance across predefined account tiers.
- UAT mode with isolated database and chat routing.
- Disaster recovery toolkit: backup, health checks, reset-state utility.
- Backtesting engine and parameter optimizer for strategy calibration.

## System Architecture Overview

### Runtime Model

- Cron-driven process model.
- One pulse per invocation.
- Explicit memory cleanup and deterministic exit.

### Concurrency and Safety

- File-lock based single-run guard through data/bot.lock.
- SQLite configured with:

```sql
PRAGMA journal_mode=WAL;
PRAGMA busy_timeout=3000;
PRAGMA foreign_keys=ON;
```

### Data and State

Primary tables:

- market_data
- signals
- zones
- kv_store
- errors

### Security

- Sensitive folders protected by Apache deny rules via .htaccess.
- Hardening script for owner-focused file permissions.
- UAT isolation for non-production dry runs.

## Technology Stack

- Language: Python 3.9+
- Web UI: Flask + Flask-Login (Passenger WSGI)
- Database: SQLite (WAL mode)
- Data sources: yfinance, FRED data pipelines
- Messaging: Telegram Bot API
- Hosting target: cPanel shared hosting (Linux)
- Tooling: argparse, requests, pandas, numpy

## Premium Web Dashboard (Sprint 42)

Sprint 42 adds a secure Flask dashboard for cPanel hosting via Passenger WSGI while preserving cron-based signal execution.

### Authentication Model

- Session authentication via Flask-Login.
- All dashboard routes require authentication.
- Unauthenticated requests redirect to `/login`.
- Default admin credentials:
  - Username: `Machete`
  - Password: `@Machete1231`

### Dashboard Routes

- `GET /login` and `POST /login`: premium login UI + credential check.
- `GET /logout`: terminate authenticated session.
- `GET /`: summary cards and recent signal overview.
- `GET /signals`: signal ledger from `signals` table in descending order.
- `GET /logs`: last 100 lines from `logs/daily-run.log` and `logs/telemetry.jsonl`.
- `GET /config` and `POST /config`: inspect and update `kv_store` keys.

### Database Lock Safety in UI

All dashboard SQL reads/writes are performed with short-lived context-managed connections:

- `with sqlite3.connect(DB_PATH, timeout=5) as conn:`
- Each request opens and closes immediately.
- This reduces lock contention against cron pulse writes.

### UI Design Direction

- Tailwind CSS via CDN (no local Node/NPM build required).
- High-end hedge-fund visual language: obsidian backgrounds, slate surfaces, cyan/gold accents.
- Premium typography, smooth transitions, responsive layout, and polished interaction states.

## Folder Structure

```text
config/              Environment and runtime configuration
data/                SQLite DB, lock file, backups, calibration artifacts
docs/                Architecture, strategy, and sprint notes
logs/                Runtime and telemetry logs
scripts/             Deployment, diagnostics, DR, optimization, utilities
src/
  alerting/          Telegram client, formatter, lifecycle manager
  analysis/          SMC + macro engines, scoring, sizing, signal factory
  backtest/          Offline CSV client and simulation engine
  core/              Orchestration, telemetry, runtime logger
  domain/            Core typed entities (Candle, Signal, etc.)
  ingestion/         Yahoo, macro, and provider clients
  persistence/       Schema and repository layer
tests/               Integration and test suites
```

## Configuration Instructions

1. Create environment file:

```bash
cp .env.example .env
```

2. Set required keys and runtime values:

- TELEGRAM_BOT_TOKEN
- TELEGRAM_CHAT_ID
- UAT_MODE and UAT_TELEGRAM_CHAT_ID (for dry runs)
- Optional fallback/provider values

3. Initialize DB schema:

```bash
python init_db.py
```

4. Apply permission hardening:

```bash
python scripts/harden_env.py
```

## Deployment Guide (cPanel)

### Prerequisites

- cPanel terminal access or SSH access.
- Python 3.9+ available on host.
- Ability to create cron jobs and configure a cPanel Python app.
- Telegram bot token and destination chat ID.

### Bot Deployment (Step by Step)

1. Upload project files to your hosting path, for example:

- /home/USERNAME/public_html/gold_trading_bot

2. Ensure hidden files are present:

- .env
- .htaccess files in protected folders

3. Create and activate the virtual environment:

```bash
cd /home/USERNAME/public_html/gold_trading_bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

4. Create required runtime directories:

```bash
mkdir -p data/backups logs config
```

5. Configure .env with production values:

- TELEGRAM_BOT_TOKEN
- TELEGRAM_CHAT_ID
- UAT_MODE=False
- Optional provider settings

6. Initialize SQLite schema:

```bash
python init_db.py
```

7. Apply permission hardening:

```bash
python scripts/harden_env.py
```

8. Run health checks:

```bash
python scripts/health_check.py
```

9. Configure cron (every minute):

```cron
* * * * * cd /home/USERNAME/public_html/gold_trading_bot && /home/USERNAME/public_html/gold_trading_bot/venv/bin/python src/bot_runner.py >> logs/cron.log 2>&1
```

10. Validate bot deployment:

- Confirm new rows in market_data.
- Confirm new signal records in signals.
- Confirm threaded Telegram lifecycle updates are arriving.

### UI Deployment (Step by Step, Passenger WSGI)

1. Confirm UI files are present:

- passenger_wsgi.py
- src/dashboard/app.py
- src/dashboard/auth.py
- src/dashboard/templates/
- src/dashboard/static/custom.css

2. In cPanel, create or open the Python Application for this project path.
3. Set startup file to passenger_wsgi.py.
4. Set application entry point to application.
5. Point the app to the same project virtual environment used by the bot.
6. Restart the Python application from cPanel.
7. Open the dashboard URL in browser.
8. Sign in using default credentials:

- Username: Machete
- Password: @Machete1231

9. Confirm route protection by opening / without login in a fresh browser session.
10. Confirm dashboard pages load:

- /
- /signals
- /logs
- /config

### Post-Deployment Troubleshooting

- DB lock issues: run scripts/reset_state.py and verify lock file behavior.
- Missing alerts: validate TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID, then run scripts/health_check.py.
- Empty ingestion: validate host network egress and Yahoo/FRED accessibility.
- UI import errors in Passenger: ensure virtual environment packages are installed and app restarted.
- Permission issues: rerun scripts/harden_env.py and verify file ownership.

## User Manual

### Bot User Manual

#### Daily Operation

1. Confirm cron is active.
2. Check runtime logs:

```bash
tail -f logs/cron.log
```

3. Confirm Telegram signal flow is healthy.

#### Routine Maintenance

1. Run health diagnostics before market sessions:

```bash
python scripts/health_check.py
```

2. Create periodic backups:

```bash
python scripts/backup_db.py
```

3. Use state reset only during incidents:

```bash
python scripts/reset_state.py
```

#### Bot Best Practices

- Keep UAT and production chat IDs separate.
- Keep UAT_MODE=False in production runtime.
- Schedule recurring backups and off-site retention.
- Avoid manual DB edits except controlled DR procedures.

### UI User Manual

#### Login and Access

1. Open the dashboard URL.
2. Sign in using default admin credentials.
3. If not authenticated, protected pages automatically redirect to /login.

#### Page-by-Page Usage

1. Dashboard (/): review signal totals, open/closed counts, macro status, and recent signals.
2. Signals (/signals): inspect full signal ledger in descending order.
3. Logs (/logs): review last 100 lines of daily-run.log and telemetry.jsonl.
4. Config (/config): update kv_store keys and values from the web form.
5. Logout (/logout): terminate current session.

#### UI Safety Notes

- Configuration updates in /config write directly to kv_store.
- Make small, intentional config edits and validate bot behavior after each change.
- Use /logs to confirm runtime impact of configuration changes.

### FAQ

Q: Can I run the bot continuously instead of cron?
A: Use cron-based stateless execution for shared-host reliability and lock safety.

Q: Why SQLite WAL instead of a server DB?
A: WAL gives strong local durability and low operational complexity for cPanel constraints.

Q: How do I test without polluting production?
A: Enable UAT_MODE and configure UAT_TELEGRAM_CHAT_ID before dry runs.

Q: How do I verify WSGI and authentication quickly?
A: Run python test_sprint42.py and follow the manual UI checklist it prints.

## Strategy Summary

- Big Bulls and Bears: trend + value-area + engulfing continuation.
- Pin Bar Rejection: wick rejection with zone confluence.
- Inside Bar Trap: false-break structure and reversal trigger.
- Confluence matrix maps conditions into 0-100 score classes.

## Operational Limits

The system is engineered for cPanel-like operational constraints:

- Approximate memory budget: 128MB.
- Pulse runtime target: complete within 60 seconds.

## Sprint 42 Verification

Run:

```bash
python test_sprint42.py
```

The script validates:

- Passenger entry import (`application` is a Flask app).
- Route protection on `/` for unauthenticated users.
- Successful authentication with default credentials.
- Failed authentication with incorrect credentials.
- Manual UI inspection checklist for premium Tailwind styling and micro-interactions.
