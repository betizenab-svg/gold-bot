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

## Still documented for a future round

1. **Trailing runner** behind swing points instead of the fixed 3R cap once
   ≥2R (Kratter/Bennett/Kiev preference — deliberately deferred: moving
   targets are hard to communicate in a signal service).

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
