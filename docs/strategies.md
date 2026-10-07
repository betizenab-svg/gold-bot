# Strategy and Scoring Specification

## The Gold 4-hour System (the only live signal source)

Code: `src/strategies/gold_system.py`. Research: `scripts/research/` (gold_lab, gold_system, gold_system_replay).

It watches gold's 4-hour chart and checks each candle when it closes. Three triggers:

1. **Breakout** (`GOLD_BREAKOUT`): the close breaks the highest high or lowest low of the previous 30 candles (about 5 trading days). Stop 1.5 ATR. Time limit 30 candles. After it fires, it waits 10 candles.
2. **Squeeze** (`GOLD_SQUEEZE`): the Bollinger bands (20, 2) are at their narrowest in 60 candles, then a close outside the band. Stop 1.5 ATR. Time limit 30 candles. Waits 6 candles.
3. **Pullback** (`GOLD_PULLBACK`): the 20, 50 and 200-candle averages are stacked in trend order; the candle dips to the 20 average and closes back in the trend direction. Stop 2 ATR. Time limit 24 candles. Waits 3 candles.

Every trade follows one fixed plan:

- Enter at market when the candle closes.
- Take the whole trade off at a single target 1R away. There is no half-close and no break-even move.
- Close at market when the time limit runs out, and always before the weekend. No new trades in the last 2 hours before the weekend.

The system's own rules:

- One signal per candle. When several triggers fire together, the order is breakout, then squeeze, then pullback.
- At most 2 open trades in the same direction.
- A trigger never stacks a second trade on its own open trade in the same direction.
- The usual risk governor still has the final say: news blackouts, loss limits, the pause switch and the weekend.

Tested on 36 months of gold, October 2023 to September 2026. Costs were included (spread and slippage), the stop was counted first whenever a candle touched both the stop and the target, and trades were resolved on 5-minute prices.

| | Trades | Win rate | Average per trade | Total |
|---|---|---|---|---|
| Whole period | 336 (about 2 a week) | 60% | +0.17R | +57.5R |

By year:

| Year | Total |
|---|---|
| 1 | +13.6R |
| 2 | +22.0R |
| 3 | +21.9R |

- **Same code through the full live pipeline** (proof engine, 4-hour candles only): 319 trades, 58% wins, +47.5R. That breaks down as +9.3R, +15.2R and +23.0R by year.
- **Buys and sells:** most of the profit came from buys, because gold rose strongly in these years. Sells roughly broke even.
- **What was tried and dropped:** a long-term trend filter (EMA 100, 200 and 300) cut profits in every version tried. RSI pullbacks, session breakouts and New York momentum did not pass.
- **Alpha Pro 8% challenge simulation** (both phases, 4% daily and 8% total loss limits):

  | Risk per trade | Passes | Typical time |
  |---|---|---|
  | 0.5% | 100% | about 62 weeks |
  | 1% | 95% | about 29 weeks |
  | 1.5% | 85% | about 18 weeks |

`GOLD_SYSTEM_ONLY` (default on) makes this the only signal source. The 5-minute gold chart then only supplies prices: it watches open trades and feeds the morning briefing. Switching it off brings back the older strategies below. They did not hold up in the 3-year tests.

### More markets? (scripts/research/portfolio.py)

The system's exact code was replayed on every other market with history. Same rules, costs, weekend close and 5-minute resolution. None passed (3-year total, then each year):

| Market | Total | Each year |
|---|---|---|
| Silver | -30R | |
| Euro | +1R | |
| Pound | -35R | |
| Yen | +14R | -9.8R in year 2 |
| Aussie | -8R | |
| US100 | +15R | negative in years 2 and 3 |
| US500 | -8R | |

Gold's trend behaviour is what makes the system work.

Only one other edge passed the strict test: buying S&P 500 dips (RSI(2) under 10 above the 200-day average, 3 ATR stop, 0.75 ATR target, 5-day limit, closed before the weekend). It made 37 trades, won 78%, and averaged +0.12R per trade, positive every year. That is only about 12 trades a year. Added to gold, it moved the typical challenge pass about 1 week sooner. For that reason it is not switched on: index contract sizes differ between brokers, and a wrong size is a bigger danger than one week.

### A strategy for every pair? (scripts/research/discover.py, breadth.py, fx_system.py)

**What was searched.** 46 strategy families on 13 markets, on 1-hour, 4-hour and daily charts:
- **Trend:** breakouts, squeezes, moving-average pullbacks and crosses, Keltner, time-series momentum, Supertrend, ADX, MACD, Ichimoku, Parabolic SAR, CCI, stochastics, Heikin-Ashi, Aroon.
- **Mean reversion:** RSI(2), Bollinger fades, z-scores, turtle soup, IBS, losing streaks.
- **Sessions:** the London breakout.
- **Built for this search:** volatility-contraction breakout, breakout retest, pullback reclaim, trend/range regime switch.
- **Markets:** gold, silver, 9 currency pairs and 2 US indices.

**How it was tested.** Each strategy was chosen on the first 24 months, with a robustness check on neighbouring settings. It was then tested on the last 12 months, which were never used to choose. Costs, the weekend close and 5-minute resolution were included throughout.

**What came out:**
- **Gold:** 17 versions survived the hidden year, all of them trend strategies.
- **Every other market:** about 9,500 versions were tested on the 12 non-gold markets, and 22 were found in the first 24 months. Every one of them lost its edge in the hidden year, so none survived.
- **The same settings on all currency pairs at once:** only my volatility-contraction breakout worked on 8 of 9 pairs in the first 24 months. It was still positive in the hidden year, but weak: +0.09R per trade, t = 1.2.
- **Added to the gold system, it made the challenge slower at equal safety.**
  - Gold alone: median 16 weeks on the hidden year.
  - Gold plus that breakout: 27 weeks.

  The extra trades are weaker and share dollar risk with gold, so the safe trade size has to shrink.
- **More gold triggers** (momentum burst, quiet-candle breakout and inside-candle breakout, each with 2R or 3R targets) made more money per year. On the challenge they were not faster at equal safety, because more trades run together. The live system stays as it is.

**Conclusion for now:** the Gold 4-hour System plus the size ladder is the best combination found. Other pairs and indices stay off until a new test finds an edge that survives a hidden period. Any market can be retested with `python scripts/research/discover.py <MARKET>`.

### Sizing for a challenge (scripts/research/sizing.py)

Same signals, walked through the real 3-year sequence from every start day (Alpha Pro 8%: +8% then +5%; fail at -8% total or -4% in one day):

| Risk per trade | Passes | Median time |
|---|---|---|
| Flat 1% | 99% | 28 weeks |
| Flat 1.25% | 95% | 21 weeks |
| Flat 1.5% | 89% | 16 weeks |
| **Ladder: 1.5%, 1% once 2% down, 0.5% once 4% down** | **100%** | **21 weeks** |

The ladder is about 7 weeks faster than flat 1% with no lower pass rate. In the bot it is the `/challenge` command.

The worst day in 3 years lost 2.06R, and at most 2 trades were open at once. The 2R daily stop (`RISK_DAILY_MAX_LOSS_R`) therefore keeps a 1.5% size inside the 4% daily limit.

Letting the same trigger stack trades gave more trades. Measured per unit of risk, it was no better, so it stays off.

## Strategy 1: Big Bulls and Bears

Trend-continuation setup built around SMA value-area pullbacks and engulfing confirmation.

### Long Conditions

1. Trend filter: latest close is above trend SMA.
2. Trigger: bullish engulfing pattern on current or prior candle.
3. Value area: trigger candle or predecessor touches value-area SMA.

### Short Conditions

1. Trend filter: latest close is below trend SMA.
2. Trigger: bearish engulfing pattern on current or prior candle.
3. Value area: trigger candle or predecessor touches value-area SMA.

### Orders

- Entry: trigger candle close.
- Stop loss:
  - Long: trigger candle low.
  - Short: trigger candle high.
- Take-profit levels are generated by the signal factory from risk distance:
  - TP1: 1.5R
  - TP2: 3.0R

## Strategy 2: Pin Bar Rejection

Reversal setup requiring a strong rejection wick plus structural zone confluence.

### Pin Bar Validation

- Compute candle range and body size.
- Bullish pin bar: lower wick length >= `PIN_BAR_TAIL_RATIO * range` and larger than body.
- Bearish pin bar: upper wick length >= `PIN_BAR_TAIL_RATIO * range` and larger than body.

### Confluence Requirement

- A compatible active/unmitigated zone must exist:
  - Bullish pin bar requires bullish zone type.
  - Bearish pin bar requires bearish zone type.
- Price must intersect zone bounds or be near a zone boundary.

### Orders

- Long:
  - Entry: candle high + entry buffer
  - SL: candle low - entry buffer
- Short:
  - Entry: candle low - entry buffer
  - SL: candle high + entry buffer
- TP1/TP2 derived by risk multiple logic in signal factory.

## Strategy 3: Inside Bar Trap

False-breakout setup around mother/inside bar structure.

### Conditions

1. Candle sequence must include mother bar, inside bar, and trap bar.
2. Inside bar is fully contained in mother bar range.
3. Trap bar breaks beyond one side of mother bar but closes back inside mother bar range.

### Direction Rules

- Bear trap (break below, close back inside): LONG.
- Bull trap (break above, close back inside): SHORT.

### Orders

- Long: entry at trap bar high, SL at trap bar low.
- Short: entry at trap bar low, SL at trap bar high.

## Confluence Scoring Matrix (0-100)

The scoring engine combines directional context into a normalized actionable score.

- Inputs:
  - Trade direction (`LONG` or `SHORT`)
  - Macro bias state
  - Current market structure state
  - Zone quality/status
  - Recent directional liquidity sweep flag

- Classification:
  - `REJECTED`: insufficient confluence.
  - `WATCHLIST`: partial alignment, not immediately tradable.
  - `ACTIONABLE`: high-confidence alignment suitable for signal creation.

A score of 100 represents maximal confluence under current rule weights. The engine classification gate controls whether the signal factory is invoked.
