# XAUUSD Signal Intelligence System Architecture

## Overview

The system uses a stateless pulse model designed for shared cPanel hosting where each run must complete quickly and release all resources. Every pulse performs ingestion, analysis, lifecycle checks, optional signal dispatch, and telemetry logging, then exits.

## Stateless Pulse Paradigm

- A cron trigger starts a short-lived process.
- The process reconstructs runtime state from SQLite and key-value rows.
- No in-memory state is relied on between runs.
- The process exits cleanly after each pulse to avoid memory growth.

This approach aligns with hosting constraints, especially a practical memory ceiling near 128MB and a strict time budget near 60 seconds per cron invocation.

## Execution Flow

1. Cron runs the bot entrypoint on schedule.
2. `src/bot_runner.py` acquires a file lock to prevent overlapping pulses.
3. `PulseOrchestrator` initializes repository and schema.
4. Live market candles are fetched and validated.
5. Analytical engines update SMC state, macro context, and setup scoring.
6. If actionable, signal payloads are persisted and dispatched to Telegram.
7. Telemetry is written and resources are released.

## File Locking and Concurrency Control

`src/bot_runner.py` uses OS-level lock semantics (`flock` on UNIX-compatible systems, compatible alternatives where available) against `data/bot.lock`.

Key guarantees:

- Single active pulse at a time.
- Avoided double-execution from overlapping cron triggers.
- Reduced DB lock contention and duplicate alerts.

## Persistence Layer and WAL

SQLite is configured with:

```sql
PRAGMA journal_mode=WAL;
PRAGMA busy_timeout=3000;
PRAGMA foreign_keys=ON;
```

WAL mode enables concurrent readers with a single writer and improves resilience under cron overlap scenarios. Busy timeout gives short, bounded retry behavior instead of immediate lock failure.

## Schema and Access Pattern

Core tables:

- `market_data`: candle history keyed by symbol/timeframe/timestamp.
- `signals`: trade signals and lifecycle status.
- `zones`: structural zones such as order blocks and FVGs.
- `kv_store`: compact state snapshots across pulses.
- `errors`: provider and runtime failures.

Indexes are created for high-frequency query fields to keep pulse latency stable.

## Security Model

- Sensitive directories (`data`, `config`, `logs`) are protected via `.htaccess` fail-closed rules.
- Environment hardening script applies owner-focused permissions.
- UAT mode isolates database and messaging targets from production.

## Deployment Topology

The repository can run under a public web root, but state-bearing files are kept non-public and blocked from HTTP access. Runtime path resolution is absolute and computed relative to project root to avoid dependency on current working directory.
