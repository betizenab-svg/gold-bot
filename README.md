# XAUUSD Signal Intelligence System

Stateless, cron-driven trading engine for XAUUSD with SQLite-backed state, non-blocking file locks, validation filters, Yahoo Finance primary ingestion, and TwelveData failover.

This README consolidates local verification, database checks, concurrency validation, soak testing, and production cPanel deployment.

## Architecture Summary

- Primary market data provider: `YahooFinanceClient`
- Secondary failover provider: `TwelveDataClient`
- State engine: SQLite in WAL mode
- Scheduler model: one pulse per cron execution
- Concurrency protection: non-blocking file lock via `src/bot_runner.py`
- Canonical market model: `Candle` with UTC Unix epoch timestamps

## Project Layout

Expected top-level structure:

- `src/`
- `config/`
- `data/`
- `logs/`
- `tests/`
- `init_db.py`
- `test_concurrency.py`

Key files:

- Runner: [src/bot_runner.py](src/bot_runner.py)
- Database bootstrap: [init_db.py](init_db.py)
- Settings: [config/settings.py](config/settings.py)
- Database config: [config/database.py](config/database.py)
- Yahoo ingestion: [src/ingestion/yahoo_client.py](src/ingestion/yahoo_client.py)
- Factory: [src/ingestion/factory.py](src/ingestion/factory.py)
- Soak test: [tests/integration/soak_test.py](tests/integration/soak_test.py)

## Environment Configuration

Configuration is loaded from `.env` automatically by:

- [config/settings.py](config/settings.py#L1-L8)
- [config/database.py](config/database.py#L1-L9)

Use [.env.example](.env.example) as your template.

### Recommended `.env`

Create a `.env` file in the project root with values like:

```dotenv
TWELVEDATA_API_KEY=
TWELVEDATA_BASE_URL=https://api.twelvedata.com
DB_PATH=data/trading_engine.db

MOCK_INGESTION=0
MOCK_SYMBOL=XAUUSD
MOCK_TIMEFRAME=M1
MOCK_CANDLES_PER_RUN=1
MOCK_DELAY_SECONDS=0
```

Notes:

- Yahoo Finance is the primary provider and does not require an API key.
- `TWELVEDATA_API_KEY` is only needed for fallback testing.
- Keep `.env` private. It is already ignored by [.gitignore](.gitignore).

---

## Step 1: Environment & Dependency Verification

Before testing logic, verify the foundation.

### 1.1 Verify directory structure

Confirm these directories exist:

- `src/`
- `config/`
- `data/`
- `logs/`

This repository already uses those locations for runtime state, logging, ingestion, and configuration.

### 1.2 Verify database file protection

The SQLite database lives under `data/`, and the directory should not be web-accessible.

Check [data/.htaccess](data/.htaccess). It should contain:

```apache
Order Allow,Deny
Deny from all
```

That denies direct HTTP access on Apache-based shared hosting.

### 1.3 Create and activate a virtual environment

#### Windows PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

#### Linux / cPanel Terminal

```bash
python3 -m venv venv
source venv/bin/activate
```

### 1.4 Install dependencies

Install the required packages for the refactored ingestion layer and environment loading:

```bash
pip install -r requirements.txt
```

At minimum, verify these are present:

- `yfinance`
- `pandas`
- `requests`
- `python-dotenv`

If installing individually:

```bash
pip install yfinance pandas requests python-dotenv
```

### 1.5 Quick provider sanity check

The new primary provider mapping is defined in [config/settings.py](config/settings.py#L11-L12):

- Internal symbol: `XAUUSD`
- Yahoo symbol: `GC=F`

---

## Step 2: Database State Initialization

Phase I relies on SQLite as the state engine.

### 2.1 Initialize the schema

Run the bootstrap script from the project root:

```bash
python init_db.py
```

This initializes the schema using [src/persistence/schema.py](src/persistence/schema.py).

### 2.2 Open the database

Default database path:

- `data/trading_engine.db`

Or, if overridden, use the value in `.env` for `DB_PATH`.

You can inspect it with:

- DB Browser for SQLite
- SQLiteStudio
- `sqlite3` command line

### 2.3 Verify required tables

Confirm these tables exist:

- `market_data`
- `kv_store`
- `signals`
- `zones`
- `errors`

Helpful SQL:

```sql
SELECT name
FROM sqlite_master
WHERE type = 'table'
ORDER BY name;
```

### 2.4 Verify WAL mode

WAL mode is enabled in [config/database.py](config/database.py#L9-L14) using:

- `PRAGMA journal_mode=WAL;`
- `PRAGMA foreign_keys=ON;`
- `PRAGMA busy_timeout=3000;`

To confirm WAL mode:

```sql
PRAGMA journal_mode;
```

Expected result:

- `wal`

To observe WAL sidecar files, access the database while it is active and check for:

- `trading_engine.db-wal`
- `trading_engine.db-shm`

These files may appear only while SQLite has active WAL activity, so if they are not visible immediately, run a pulse and re-check while the process is active.

---

## Step 3: Manual Pulse Dry Run

Run one full atomic pulse through the master wrapper.

### 3.1 Execute the runner

From the project root:

```bash
python src/bot_runner.py
```

### 3.2 Check the log file

Open:

- [logs/daily-run.log](logs/daily-run.log)

You should see a sequence similar to:

- `Lock acquired`
- `---- Pulse started ----`
- `Pulse start: ...MB` or memory unavailable on Windows
- `Selected ingestion client: YahooFinanceClient`
- `Candles received: ...`
- `Dropped ... invalid candles` if zero-volume ghost ticks were filtered
- `Pulse finished in ...s`
- `Lock released`

Important note:

- On Windows, the telemetry module can log memory usage as unavailable because the `resource` module is POSIX-only.
- On Linux shared hosting, memory usage in MB should be logged.

### 3.3 Verify memory posture

On Linux or cPanel, inspect log entries generated by [src/core/telemetry.py](src/core/telemetry.py).

Target:

- Stay comfortably below the 128MB shared-hosting ceiling.

The logger warns if memory crosses the warning threshold.

---

## Step 4: Incremental Fetching & Data Integrity Check

The bot must fetch only new data, not full history, on each pulse.

### 4.1 Check watermark state

Query `kv_store`:

```sql
SELECT key, value, updated_at
FROM kv_store
WHERE key LIKE 'last_fetch_%' OR key = 'last_processed_timestamp'
ORDER BY key;
```

Look for keys such as:

- `last_fetch_XAUUSD_H1`
- `last_processed_timestamp`

Expected value:

- UTC Unix epoch integer stored as text

### 4.2 Run the pulse again

```bash
python src/bot_runner.py
```

### 4.3 Verify the second run is incremental

Expected behavior:

- It should fetch only candles newer than the watermark.
- On short intervals, this may be `0` or a very small number of new candles.

Use this SQL to verify insert growth:

```sql
SELECT symbol, timeframe, COUNT(*) AS candles, MIN(timestamp), MAX(timestamp)
FROM market_data
GROUP BY symbol, timeframe;
```

### 4.4 Verify canonical schema storage

Query the latest rows:

```sql
SELECT symbol, timeframe, timestamp, open, high, low, close, volume
FROM market_data
ORDER BY timestamp DESC
LIMIT 10;
```

Verify:

- `symbol` is `XAUUSD`
- `timestamp` is an integer Unix epoch
- no Yahoo symbol such as `GC=F` is stored in `market_data`

---

## Step 5: Concurrency & Lock Verification

This system must reject overlapping runs safely.

### 5.1 Run the automated concurrency test

From the project root:

```bash
python test_concurrency.py
```

The test launches two near-simultaneous runner instances and verifies:

- second process exits with code `0`
- second process exits quickly
- no stdout/stderr noise
- only one `Lock acquired` event is recorded

### 5.2 Manual concurrency check

Open terminal window 1:

```bash
python src/bot_runner.py
```

Immediately open terminal window 2 and run the same command:

```bash
python src/bot_runner.py
```

Expected result:

- first instance acquires the lock and runs
- second instance exits immediately with code `0`
- only one overlapping lock acquisition should appear in the log

Check [logs/daily-run.log](logs/daily-run.log) for:

- one `Lock acquired`
- one `Lock released`
- one or more `Lock acquisition failed` lines for rejected overlap attempts

This behavior protects the server from the thundering herd problem.

---

## Step 6: Soak Test / Stress Validation

Run the Phase I soak test to validate long-running stability.

### 6.1 Execute the soak test

```bash
python tests/integration/soak_test.py
```

This test rapidly launches many instances using mock ingestion and verifies:

- no `database is locked` failures
- lock file is cleaned up properly
- timestamps remain contiguous
- watermark matches the newest candle
- duplicate primary keys are not introduced
- parent memory footprint does not trend upward unexpectedly

### 6.2 Pass criteria

The soak test should complete without:

- `OperationalError: database is locked`
- orphaned lock files
- malformed timestamp sequences
- missing watermark updates

If the test passes, your WAL plus `busy_timeout=3000` combination is behaving correctly under pressure.

---

## Step 7: cPanel Production Deployment

Once the local checks pass, deploy the bot as a scheduled state machine.

### 7.1 Prepare the deployment folder

On cPanel, place the project outside `public_html` for security.

Recommended structure:

```text
/home/username/xauusd_bot/
```

Why:

- keeps SQLite, logs, and `.env` out of the public web root
- reduces accidental exposure risk

### 7.2 Upload the project

Use one of:

- cPanel File Manager
- SFTP
- Git deployment if available

Upload:

- `src/`
- `config/`
- `data/`
- `logs/`
- `tests/` if you also want server-side validation
- `requirements.txt`
- `init_db.py`
- `.env`

Do not upload:

- local virtual environment directories like `.venv/`
- temporary test databases unless needed

### 7.3 Create the Python environment on the host

If your host provides **Setup Python App**:

1. Open **cPanel → Setup Python App**.
2. Create a new application.
3. Choose the Python version supported by your host.
4. Set the application root to your bot folder, for example:
   - `/home/username/xauusd_bot`
5. Create the environment.

If your host provides terminal access instead:

```bash
cd /home/username/xauusd_bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 7.4 Configure `.env` in production

Create `/home/username/xauusd_bot/.env`.

Recommended starting values:

```dotenv
TWELVEDATA_API_KEY=
TWELVEDATA_BASE_URL=https://api.twelvedata.com
DB_PATH=/home/username/xauusd_bot/data/trading_engine.db
MOCK_INGESTION=0
```

Use an absolute `DB_PATH` in production.

### 7.5 Initialize the database on the host

Run:

```bash
/home/username/xauusd_bot/venv/bin/python /home/username/xauusd_bot/init_db.py
```

Then confirm:

- database file exists
- `data/` is writable
- `logs/` is writable

### 7.6 Set correct permissions

Ensure the cPanel user can write to:

- `data/`
- `logs/`

Typical ownership is already correct if the files were uploaded by the same user.

### 7.7 Configure the cron job

Open:

- **cPanel → Cron Jobs**

Create a new cron entry:

- Schedule: `Once Per Minute`
- Cron expression: `* * * * *`

Command format:

```bash
/home/username/xauusd_bot/venv/bin/python /home/username/xauusd_bot/src/bot_runner.py >> /dev/null 2>&1
```

Replace paths to match your account.

What this does:

- starts one pulse every minute
- uses the runner wrapper rather than calling the orchestrator directly
- suppresses cron email noise by redirecting output

### 7.8 Why the runner is the correct cron target

Always point cron to [src/bot_runner.py](src/bot_runner.py), not directly to the orchestrator.

The runner provides:

- non-blocking lock acquisition
- lock release cleanup
- file-based logging
- safe overlap rejection

### 7.9 First production validation checklist

After enabling the cron job:

1. Let it run for 10 to 60 minutes.
2. Download or inspect [logs/daily-run.log](logs/daily-run.log).
3. Confirm recurring entries for:
   - `Lock acquired`
   - pulse start/end
   - `Selected ingestion client: YahooFinanceClient`
4. Inspect the database and confirm fresh rows are appearing.
5. Confirm watermark keys continue advancing.
6. If TwelveData fallback is configured, inspect the `errors` table for provider failures.

### 7.10 Production troubleshooting

If the bot does not pulse in cPanel:

- verify the Python binary path is correct
- verify the project path is correct
- verify `.env` exists in the project root
- verify `data/` and `logs/` are writable
- verify dependencies are installed in the same virtual environment used by cron
- inspect the cron job command manually in SSH or Terminal first

Example manual production run:

```bash
/home/username/xauusd_bot/venv/bin/python /home/username/xauusd_bot/src/bot_runner.py
```

---

## Recommended Verification Commands

Local verification flow:

```bash
python init_db.py
python test_sprint3_refactor.py
python test_concurrency.py
python tests/integration/soak_test.py
python src/bot_runner.py
```

If you want live provider validation:

```bash
python ingest_test.py
```

---

## Security Notes

- Keep `.env` out of version control.
- Keep the project outside `public_html` in production.
- Keep [data/.htaccess](data/.htaccess) in place on Apache hosting.
- Do not store external provider symbols like `GC=F` in your canonical tables.
- Use TwelveData API keys only in `.env`, never in committed files.

---

## Success Criteria

Your Phase I environment is healthy when all of the following are true:

- dependencies install cleanly
- schema initializes successfully
- WAL mode is active
- `YahooFinanceClient` is selected as the primary provider
- `market_data` stores canonical `XAUUSD` candles with integer timestamps
- repeated pulses fetch incrementally
- concurrent runs do not overlap dangerously
- soak tests complete without lock or memory issues
- cPanel cron executes one clean pulse per minute

Once those conditions hold, the system is ready for continuous shared-host deployment.
