# Knowledge Base — How the 21 Books Shaped the Engine

Every book in `books/` was read in full (via three parallel research passes),
rules were cross-checked between books, and the consensus was encoded. This
file maps each decision to its sources so future tuning stays evidence-based.

`books/` is gitignored (copyrighted) — this map is the shareable artifact.

## Encoded rules and their sources

| Code location | Rule | Source books (consensus) |
|---|---|---|
| `strategies/pin_bar_rejection.py` | Grade A pin: tail ≥ 66% of range, close in far third, opposite wick ≤ 20% | Candlestick Bible (shadow ≥ 2× body); Trendline Trading; Market Traps |
| `strategies/pin_bar_rejection.py` | Grade B "Brooks reversal bar": tail 33-66%, body ≥ 40%, close beyond prior close | Al Brooks *Trading Price Action Reversals* |
| `strategies/pin_bar_rejection.py` | Doji veto: small mid-range body is never a signal | Brooks; Market Traps; Candlestick Bible |
| `strategies/inside_bar_trap.py` | Trap = break of inside-bar structure that closes back inside mother range; STOP entry beyond trap bar | Candlestick Bible; Market Traps ("1-tick trap"); Brooks (failed breakouts) |
| `analysis/market_state.py` | Barbwire: 3+ mutually overlapping bars → no breakout entries, penalized limit entries | Brooks ("tight trading range trumps everything"); Market Traps ("never initiate in the middle of a range") |
| `analysis/market_state.py` | Climax veto: 3 consecutive ≥1.5×ATR same-direction bars → no with-trend entries | Brooks (consecutive climaxes → 2-leg correction); Market Traps (giant bar after protracted move = trap); Dayton (shortening of thrust) |
| `analysis/market_state.py` | Giant-bar veto: >2.25×ATR bar → no breakout STOP entries | Trendline Trading (skip long breakout candles); Brooks/Traps (location-dependent exhaustion) |
| `analysis/sessions.py` | Killzones: London 07-10 UTC and NY 12-15 UTC weighted highest; off-session penalized; late-Friday penalty | ICT/Market Makers Method (killzones); Brooks Ch.11; Market Traps (morning-only trading); Set-and-Forget (avoid Asian-formed zones, Sunday/late-Friday) |
| `analysis/momentum.py` | RSI 72/28 exhaustion veto = "never chase" | Market Makers Method (never chase >100-pip moves); Boxer ("too far too fast"); Boroden (tighten at 1.272 ext); Brooks (20-gap-bar) |
| `analysis/momentum.py` | EMA 21/55 trend filter (filters only, never triggers) | Boxer (10/21/50 ≈ fib 8/21/55); Kennedy (crossovers whipsaw — use as filter); Set-and-Forget dissent noted |
| `analysis/confluence.py` | OTE bonus: entry inside 61.8-78.6% of last swing leg | Market Makers Method (OTE 62-79%); Boroden (.618/.786 retracement clusters) |
| `analysis/adaptive_weights.py` | Weight strategies by rolling EXPECTANCY in R, not win rate; 10-trade floor, full authority at 20 | Bennett ("win rate doesn't matter, payoff does"); Kiev (masters right ~50%, 3-10% of trades = all profit); Link (W×R framework); Bassal (don't judge a plan on a few trades) |
| `analysis/risk_governor.py` | Max 6 signals/day; 45-min cooldown after SL; 3-SL halt 6h | Link (his plan: 5/day, stop after losses); Inv./MASTER (1-3/day); Langer (stop after 3 consecutive losses) |
| `analysis/risk_governor.py` | Tier-2: 5 consecutive SL → 24h suspension | Link (4 losers = done for the day; 10 straight = full stop and review) |
| `analysis/risk_governor.py` | Daily circuit breakers: stop at −3R day; lock the day at +4R | Link (3-tier daily ladder, giveback caps); Kiev (daily shutdown rule); Bennett ("Ferrari trade" overconfidence guard) |
| `analysis/risk_governor.py` | News blackout: no signals 30 min before / 15 min after high-impact events (kv `upcoming_news_events_json`) | Link (FOMC sidelines); Set-and-Forget (NFP/FOMC/CPI day off); MASTER (±1h); Langer (2-3h red-flag buffer) |
| `analysis/signal_factory.py` | Minimum stop = max($3, 1×ATR); ATR multiplier 1.5 | All six trading-method books: stops beyond structure with buffer, never tight |
| `analysis/signal_factory.py` | Round-number clearance: stops pushed off the $5 grid | Market Makers Method (00/20/80 traps); Bassal ($49.83 not $50); Boxer (round-number acceleration) |
| `alerting/lifecycle_manager.py` | STOP vs LIMIT activation semantics per order type | Brooks/Bible/Trendline (stop 1 tick beyond signal bar); Set-and-Forget (limit at fresh zones) — contradiction resolved: limit only for fresh with-trend zones, stop for breakouts |
| `alerting/lifecycle_manager.py` | Half off at TP1 (1.5R), runner to breakeven | Link (BE at 50% of target); Bassal (sell half, ride rest); Kiev (scale out approaching target); Set-and-Forget (partials over early BE) |
| `alerting/lifecycle_manager.py` | Pending expiry 90 min (≈18 M5 bars) | Market Traps (failed breakouts die in 3-5 bars); Trendline (false breaks declare in 1-3 candles); Bassal (signal from 3 bars ago is not a signal) |
| `alerting/lifecycle_manager.py` | Time stop: ACTIVE trade unpaid after 24h closed flat; runner exempt | Bassal (10-day rule, scaled); Kratter (1-2 bar thesis test); Kiev (fat-tail runners must never be capped) |
| `analysis/scoring.py` + TP structure | TP1=1.5R floor, TP2=3R; minimum blended R:R ≈ 2.25 | Traps/Bible (min 2:1); Set-and-Forget (3:1); Link (3:1, W×R ≥ 0.5); SPINE (edge = R:R × W/L) |

## Round 2 — implemented from the "next iterations" list

| Code location | Rule | Source books |
|---|---|---|
| `analysis/mitigation.py` | Zone freshness: first touch mitigates, second touch consumes (INVALIDATED) | Set-and-Forget (fresh/non-fresh/used-up tiers); RTM (First Time Back); MMM (mitigation happens once) |
| `analysis/structure.py` + orchestrator | BOS needs two consecutive closes beyond the swing — one-bar breaks are traps | Market Traps ("any break has to be confirmed by 2 bars"); Brooks (follow-through bar); SPINE; Trendline Trading |
| `analysis/signal_factory.py` | TP2 capped at the measured move of the prior leg, shaved by 0.1 ATR | Market Traps (AB=CD); SPINE (pattern-height targets, "give a few pips"); Brooks (measured moves); Boroden (extensions) |
| orchestrator + `analysis/confluence.py` | Second attempt at the same level within 3-20 bars scores +10 | Brooks ("the second signal is more reliable"); Market Traps ("wait for double failures") |
| `analysis/market_state.py` | Day-extension veto: no continuation entries once the day has run >2.5x the Asian range | Market Makers Method (Central Bank Dealer Range 2-3x); Boxer ("too far too fast") |
| orchestrator + `analysis/confluence.py` | SMT divergence: 20-day gold-vs-inverted-DXY spread z-score; stretched (z >= 2) favors the mean-reversion side (+/-5) | Market Makers Method (SMT); Correlation Secret (reversion when correlation breaks) |
| `alerting/lifecycle_manager.py` + schema | MFE/MAE per trade recorded in R (ratcheted per pulse) for empirical stop/target calibration | Kiev + Link metrics catalogs |
| `analysis/trendline.py` + confluence | Counter-trend entries require a prior body-close break of the trendline drawn through the last two swing pivots | Brooks ("the single most important rule"); Market Traps; Trendline Trading; Candlestick Bible |
| `scripts/calibrate_from_history.py` | Evidence loop: per-strategy expectancy, profit factor, loser-MFE and winner-MAE medians with concrete knob recommendations | Link/Kiev statistics reviews; Bassal (change the plan only on evidence) |

## Round 3 — second-pass extraction + external sources (BabyPips)

| Code location | Rule | Source |
|---|---|---|
| `strategies/engulfing_zone.py` | Engulfing at a zone as a standalone trigger: Nison's 3 criteria (definable prior leg via EMA21 side, full body engulfment, opposite colors) + zone confluence, STOP beyond the extreme | Candlestick Bible pp109-135; Master the Art; Nison criteria |
| `strategies/pullback_h2.py` | Brooks H2/L2: second leg of a with-trend pullback, STOP beyond the H2/L2 bar, signal-bar quality gate | Brooks bar-counting chapters + guidelines 42/61 ("you will not make money until you trade with-trend pullbacks") |
| `analysis/pivots.py` | Classic floor pivots PP/R1-R3/S1-S3 from previous UTC day; entry near a supportive pivot = +6 | BabyPips School of Pipsology (formulas fetched 2026-08) |
| `analysis/momentum.py` | Brooks G73: 7 of last 10 closes above EMA21 = no shorts (mirror for longs) | Brooks Ch 25 guideline 73 |
| `analysis/sessions.py` | London-to-NY continuation: NY-killzone signals aligned with London's net direction +5, fighting it -5 | Market Makers Method ("the direction taken in London often continues in New York") |
| `analysis/risk_governor.py` | Manual kill switch (kv `trading_paused`) togglable from the dashboard | Link/Bassal discipline chapters (the plan owner can always stand down) |
| `analysis/signal_factory.py` `_render_trade_plan` | Telegram reasoning restructured as a professional trade plan: tier, top-down context, location, liquidity story, trigger, evidence, numbers with WHY, risk state, if-then plan, invalidation | Link (written if/then scenarios), Kiev (conviction tiers, "what proves me wrong"), Bassal (journal format), Bible (trend-level-signal triad) |
| `src/dashboard/` | Performance page (equity curve in R, per-strategy expectancy/profit factor, MFE/MAE, calibration recommendations), Risk page (governor state, kill switch, news blackout editor), Market page (macro intelligence, zone book, session clock) | Link/Kiev statistics reviews; Bassal monthly review |

## Round 4 — finishing the documented backlog

| Code location | Rule | Source |
|---|---|---|
| `alerting/lifecycle_manager.py` | Structure exit: after TP1, a confirmed structure flip against the runner closes it at market (banked half kept, runner marked in R) | Brooks (trail by structure), Boroden (exit on pattern flip), Master the Art ("if the 15-minute breaks a previous low, exit immediately") |
| `strategies/quasimodo.py` | Quasimodo: sweep of the prior swing + neckline break arms a LIMIT back at the left shoulder, stop beyond the head | Reading the Market (QM section) |
| `analysis/confluence.py` `_three_push_exhaustion` | Three pushes with shrinking thrust veto with-trend entries | Brooks (wedge/shrinking stairs), Dayton (shortening of the thrust) |
| `analysis/confluence.py` `_two_bar_reversal_evidence` | Two-bar reversal as +4 evidence (opposite similar bodies, second reclaims the first's open) | Brooks (two-bar reversals as buying/selling pressure), Candlestick Bible (tweezers) |
| `analysis/position_sizing.py` + factory | Conviction-tiered sizing: Tier 1 (score >= 85) sizes the lot table at 2% risk, Tier 2 at 1% | Kiev (conviction tiers), Link (size by setup quality), Bassal |

## Replay evidence (45 days of real M5 data, full pipeline, 2026-08-15)

The replay engine (`scripts/replay.py`) runs the ACTUAL production pipeline
pulse-by-pulse over history. Three runs on identical data:

| Configuration | Net R | Full-win rate | What changed |
|---|---|---|---|
| Baseline | -10.25R | 17.9% | as-built |
| Fix 1+2 | -0.75R | 17.2% | Quasimodo zone gate (RTM's own rule) + breakeven protect at +1R (Trendline/Brooks rule) |
| Fix 3 | **+9.75R** | **30.8%** | QUASIMODO quarantined (negative expectancy over 31 trades across both runs -> Link's quarantine rule) |

Per-strategy verdicts (final run): H2_PULLBACK +1.28R/trade PF 11.25 (the
books' "most reliable trade" confirmed), L2 +0.13R, ZONE_BOUNCE +0.11R,
INSIDE_BAR ~flat, PIN_BAR -0.29R over 7 (small sample, monitoring).
Quarantine is config: `DISABLED_STRATEGIES` in .env; live adaptive weights
continue to referee every strategy from real outcomes.

Re-run the validation anytime: `python scripts/replay.py --days 45`, or the
"Replay Validation" workflow on GitHub (report as downloadable artifact).

## Multi-asset replay evidence (45 days, full pipeline, 2026-08-22)

Every market earned (or lost) its live-signal seat through the same replay:

| Market | Config | Net R | Full-win rate | Verdict |
|---|---|---|---|---|
| XAUUSD | M5, re-validated post-refactor | **+8.75R** | 25.0% | LIVE (H2 pullback +1.06R/trade, PF 5.75) |
| BTCUSD | M5 (first attempt) | -16.50R | 0.0% | REJECTED - crypto chop shreds M5 patterns |
| BTCUSD | M15 (instrument override) | **+10.25R** | 43.8% | LIVE on M15 (zone bounce PF 2.35, pin bar PF 3.0) |
| EURUSD | M5, gold-scale $0.50 buffer bug | +5.00R | 28.6% | bug: 157 cancelled entries, only zone-bounce traded |
| EURUSD | M5, per-instrument buffers | **+25.25R** | 26.6% | LIVE (pin bar +0.28R/trade over 77, PF 1.64) |
| GBPUSD | M5, per-instrument buffers | **+9.25R** | 30.8% | LIVE (inside-bar trap +1.22R/trade, PF 10.75) |

Combined: +53.5R across the four markets over the same 45 days.

The BTC and EURUSD rows are the whole argument for evidence-first releases:
book-derived defaults looked reasonable and were catastrophically wrong in
one case and half-crippled in the other. The fix set: per-instrument entry
buffers and zone proximity (`config/instruments.py`), per-instrument signal
timeframes (BTC M15), and volume-optional sweep detection for FX feeds.

## Breakeven-arm tuning (live-loss hypothesis, replay-confirmed 2026-08-26)

First live week: 4 of 4 losers ran +0.30R to +0.84R favorable before dying.
Hypothesis: arm breakeven protection at +0.75R instead of +1.0R. Tested on
identical 45-day data before shipping:

| Market | BE at 1.00R | BE at 0.75R | Verdict |
|---|---|---|---|
| EURUSD | +25.25R | **+29.50R** | +17% (5 fewer stops, 13 more breakevens) |
| BTCUSD | +10.25R | **+12.25R** | +20% |
| XAUUSD | +8.75R | **+14.25R** | +63% (same wins, 2 fewer stops, 6 more BEs) |

Shipped as the default (`BE_ARM_R`, env-overridable). GBP verification
via the weekly scheduled cloud replay (Yahoo rate limits blocked local runs);
the workflow now accepts a `be_arm_r` input for future experiments.

## Deliberately not implemented (and why)

1. **Range-day edge-fade strategy family** — the barbwire veto, day-extension
   veto and failed-breakout traps already cover the defensive half; the
   offensive half (fading range edges) needs live range-boundary tracking
   that should be calibrated against real MFE/MAE data first.
2. **Symmetry projections / full fib clusters** — the OTE band plus measured
   moves capture most of the value; clusters add precision only after the
   evidence loop shows where entries are missing by small margins.
3. **Morning/evening star detectors** — the books themselves warn these lose
   ~20% reliability below H4; on an M5 pipeline the pin-bar grades and
   engulfing detector are the appropriate versions of the same idea.

## Contradictions resolved (so nobody "fixes" them backwards)

- **Touch-entry vs confirmation-entry**: limit orders only for fresh
  with-trend zones; everything else needs the breakout trigger.
- **Win rate vs expectancy**: expectancy won (see adaptive weights).
- **Giant bar = strength vs trap**: location decides; we veto only late/
  breakout cases.
- **Fib levels do not flip polarity** (Boroden) but structural zones do —
  flips apply to zones only.
- **Tight trailing**: rejected; books agree premature BE/trailing bleeds
  winners (we move to BE only after TP1 is banked).
