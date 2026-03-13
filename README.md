# XAUUSD Signal Intelligence System

## Project Overview

XAUUSD Signal Intelligence System is a stateless, cron-driven trading automation platform for gold (XAUUSD). It ingests live market and macro data, applies Smart Money Concepts (SMC) and macro-fundamental filters, scores opportunities, and publishes threaded Telegram alerts.

The project is optimized for shared cPanel hosting constraints, including strict runtime limits and low memory budgets.

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
- Ability to create cron jobs.
- Telegram bot token and destination chat ID.

### Step 1: Upload Files

1. Upload project to target path, for example:

- /home/USERNAME/public_html/gold_trading_bot

2. Ensure hidden files are included (.env, .htaccess where relevant).

### Step 2: Create Virtual Environment

```bash
cd /home/USERNAME/public_html/gold_trading_bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Step 3: Prepare Directories

```bash
mkdir -p data/backups logs config
```

### Step 4: Configure Environment Variables

Create .env with production values:

- TELEGRAM_BOT_TOKEN
- TELEGRAM_CHAT_ID
- UAT_MODE=False
- Optional provider settings

### Step 5: Initialize Database

```bash
python init_db.py
```

### Step 6: Apply Hardening

```bash
python scripts/harden_env.py
```

### Step 7: Verify Health

```bash
python scripts/health_check.py
```

All checks should return [OK].

### Step 8: Configure Cron

Use 1-minute interval:

```cron
* * * * * cd /home/USERNAME/public_html/gold_trading_bot && /home/USERNAME/public_html/gold_trading_bot/venv/bin/python src/bot_runner.py >> logs/cron.log 2>&1
```

### Step 9: Final Verification

- Confirm market_data rows are updating.
- Confirm signals and lifecycle updates are being recorded.
- Confirm Telegram thread replies are grouped under original signal messages.

### Step 10: Enable Passenger WSGI Dashboard (cPanel)

1. Ensure these files exist:
  - `passenger_wsgi.py`
  - `src/dashboard/app.py`
  - `src/dashboard/templates/*`
  - `src/dashboard/static/custom.css`
2. In cPanel Python app configuration, set startup file to `passenger_wsgi.py`.
3. Ensure Passenger points to the project root and Python virtual environment.
4. Restart the Python application from cPanel.
5. Open the dashboard URL and sign in with default admin credentials.

### Troubleshooting Tips

- DB lock issues: run scripts/reset_state.py and check lock file behavior.
- Missing alerts: validate TELEGRAM_BOT_TOKEN and chat ID, run health_check.
- Empty ingestion: validate host network egress and Yahoo availability.
- Permission issues: rerun harden_env and check owner/group permissions.

## User Manual

### 1. Access and Startup

1. Ensure cron is configured and enabled.
2. Monitor logs:

```bash
tail -f logs/cron.log
```

### 2. Configure the Platform

1. Edit .env for production or UAT mode.
2. Run init_db.py for first-time schema setup.
3. Run harden_env.py after deployment or permission drift.

### 3. Use Major Features

- Live signal generation: automatic via cron pulse.
- Lifecycle tracking: TP/SL updates posted as threaded replies.
- Backup: run scripts/backup_db.py.
- Health diagnostics: run scripts/health_check.py.
- Emergency reset: run scripts/reset_state.py.
- Strategy calibration: run scripts/optimize_params.py on historical CSV input.

### 4. Example Workflow

1. Deploy to cPanel and configure cron.
2. Run health_check and confirm all [OK].
3. Observe first alerts in Telegram.
4. Run periodic backup_db jobs.
5. In anomaly scenarios, execute reset_state and verify recovery.

### 5. Best Practices

- Keep UAT and production chat IDs separate.
- Keep UAT_MODE=False in production runtime.
- Schedule recurring backups and off-site retention.
- Review logs daily and health checks before market sessions.
- Avoid manual DB edits except controlled DR procedures.

### 6. FAQ

Q: Can I run the bot continuously instead of cron?
A: Use cron-based stateless execution for shared-host reliability and lock safety.

Q: Why SQLite WAL instead of a server DB?
A: WAL gives strong local durability and low operational complexity for cPanel constraints.

Q: How do I test without polluting production?
A: Enable UAT_MODE and configure UAT_TELEGRAM_CHAT_ID before dry runs.

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
