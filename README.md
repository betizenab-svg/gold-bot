# XAUUSD Signal Intelligence System

The system is a stateless, cron-driven Pulse architecture. Every run of `src/bot_runner.py` performs one atomic pulse, persists validated data to SQLite (WAL mode), computes macro state, and exits.

Primary live data stack (keyless):

- Market data: Yahoo Finance (`yfinance`) for XAUUSD and DXY.
- Macro yield data: FRED (`pandas-datareader`) for U.S. 10Y TIPS (`DFII10`).

Optional fallback and future integrations:

- TwelveData API key is only used as a secondary fallback provider.
- Telegram token is reserved for future sprints.

## Step 1: Environment Setup

### 1.1 Clone and enter project

```bash
git clone <your-repo-url>
cd gold_trading_bot
```

### 1.2 Create and activate virtual environment

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Linux/macOS/cPanel Terminal:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 1.3 Install dependencies

```bash
pip install -r requirements.txt
```

Required packages include:

- `yfinance`
- `pandas`
- `pandas-datareader`
- `requests`
- `python-dotenv`

### 1.4 Configure environment variables

Copy template:

Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

Linux/macOS:

```bash
cp .env.example .env
```

Edit `.env` and set values as needed. Important:

- Primary Yahoo/FRED path is keyless and works without API keys.
- `TWELVEDATA_API_KEY` is optional fallback only.

### 1.5 Ensure `data/` exists and is protected

Create directory if missing:

```bash
mkdir -p data
```

Create `data/.htaccess`:

```apache
Order Allow,Deny
Deny from all
```

This prevents direct web access when deployed on Apache-based hosting.

## Step 2: Database Initialization

Initialize the SQLite schema (WAL-ready runtime database):

```bash
python init_db.py
```

Expected database location:

- `data/trading_engine.db`

Verify core tables:

```sql
SELECT name
FROM sqlite_master
WHERE type='table'
ORDER BY name;
```

You should see at least:

- `market_data`
- `kv_store`
- `signals`
- `zones`
- `errors`

## Step 3: Phase 1 Verification (Live Data Pulse)

### 3.1 Run one pulse manually

```bash
python src/bot_runner.py
```

### 3.2 Check runtime log

Open:

- `logs/daily-run.log`

Confirm log indicators:

- Pulse started and completed.
- Memory snapshot line (`Pulse start` / `Pulse end`).
- Yahoo ingestion selected or successful fetch behavior.
- Candle validation/persistence flow.

### 3.3 Verify real market rows in SQLite

Run:

```sql
SELECT symbol, timeframe, timestamp, open, high, low, close, volume
FROM market_data
WHERE symbol='XAUUSD'
ORDER BY timestamp DESC
LIMIT 20;
```

Validation expectations:

- `timestamp` values are real UTC Unix integers.
- `open/high/low/close/volume` are numeric floats.
- Data is not synthetic/mock.

## Step 4: Phase 2 Verification (Macro Engine)

Macro computation is gated to a 24-hour loop (cache TTL). During macro update, the orchestrator:

- Pulls FRED 10Y TIPS yield (`DFII10`).
- Pulls DXY daily closes from Yahoo Finance.
- Computes and stores macro state values in `kv_store`.

### 4.1 Trigger macro update

Run pulse:

```bash
python src/bot_runner.py
```

If run again inside 24 hours, macro update may be skipped by design.

### 4.2 Verify macro keys in `kv_store`

```sql
SELECT key, value, updated_at
FROM kv_store
WHERE key IN (
  'macro_regime',
  'macro_dxy_correlation',
  'macro_long_bias_multiplier',
  'macro_tips_correlation',
  'last_macro_update_timestamp'
)
ORDER BY key;
```

Validation expectations:

- `macro_regime` is populated (string regime label).
- `macro_dxy_correlation` is a real float value.
- `macro_long_bias_multiplier` is a real float value.
- `macro_tips_correlation` is populated from live FRED+Gold alignment.

## Step 5: Dynamic Calibration Testing

Run calibration script:

```bash
python scripts/calibrate_regime.py
```

Verify output file exists:

- `data/calibration_report.csv`

Check most recent rows include dates up to current period:

```bash
python - <<'PY'
import pandas as pd
p = 'data/calibration_report.csv'
df = pd.read_csv(p)
print(df.tail(5))
PY
```

Expected result:

- Report is generated successfully.
- Final rows are current and not stale.

## Step 6: cPanel Cron Deployment

Use a one-minute cron pulse in production:

```cron
* * * * * /path/to/venv/bin/python /path/to/src/bot_runner.py
```

Example (replace with your real absolute paths):

```cron
* * * * * /home/username/gold_trading_bot/.venv/bin/python /home/username/gold_trading_bot/src/bot_runner.py
```

Deployment notes:

- Keep project outside public web root when possible.
- Keep `.env`, database, and logs private.
- Ensure write permissions for `data/` and `logs/`.

## Notes On Live-Only Operation

- Primary ingestion (Yahoo + FRED) requires no API keys.
- COT and calendar processing no longer use random mock generators.
- If live calendar/COT parsing is unavailable, manual DB override keys can be used:
  - `manual_calendar_events_json`
  - `manual_cot_net_positions_json`
