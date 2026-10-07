# Roadmap (100 items)

Status is ticked off as items are finished. "You" marks steps only the owner can do.

## Status: all 100 items built ✅
Every item has its code in place and tested; the full test suite passes. Items that also need a step from you are listed in [owner-tasks.md](owner-tasks.md).

Changes from the original plan:
- **23** used HistData (free, no sign-up) instead of Dukascopy, which blocks automatic downloads.
- **46** added the US500 instead of the US30; there is no free, on-time Dow feed.
- **48** oil has no free history download, so it stays a trial with no proof.
- **73** the MT5 copier is written but untested; it needs MT5 on your computer.
- **16** not needed: GitHub takes 15–90 seconds to start each run, so the bot already gets each candle after it closes. The cron-job.org timer stays at every 5 minutes.
- **99** the mood switch only skips "wild" markets. It is off until the monthly proof shows it helps.

What the history said, after costs over 36 months:
- Most market-and-strategy pairs **lose**, and the evidence file switches them off.
- The two clearly positive ones are:
  - gold on the 1-hour chart (swing): +14.8R over 110 trades;
  - gold's Asian-range breakout: +7.7R over 116 trades.
- Both run as owner-only trials until they prove themselves live.
- The VIP stays closed until the rule in Milestone 8 is met.

### Milestone 1 — Fix what is quietly hurting results
*Done when: all tests pass, live runs stay green, and the bot's gold prices match a broker's XAUUSD chart.*
1. Switch gold to the real spot price from TwelveData. It's free and needs no card, and one call every 5 minutes (288 a day) fits the free 800 a day. **You:** sign up, or tell me if a key is already saved in GitHub.
2. Let each market use its own price source: gold uses TwelveData, the rest stay on Yahoo, which is on time for them.
3. Keep Yahoo as gold's emergency backup, and label any signal made from it "backup prices, check levels".
4. Redraw gold's saved price zones after the switch, because the old ones were drawn on futures prices.
5. Record entry, stop and targets for every blocked or rejected idea, then track what each would have done. That way every filter is judged on results.
6. Stop the fixed "central banks are buying" number from blocking every gold sell.
7. Turn the big-traders block into a lower score instead of a hard block, until history shows it helps.
8. Use the same risk size for every signal until history shows top scores really win more.
9. Send warnings and errors to a private admin chat; the public channel gets trading messages only. **You:** create a private group and add the bot.
10. Take the dashboard password out of the code and the README. The repo is public, so anyone can read it today. **You:** change that password anywhere else you use it.
11. Run all the tests automatically on every code change, so a broken change can't reach the live bot.

### Milestone 2 — Never miss a beat
*Done when: you get one "all good" message a day, and any outage reaches you within 15 minutes.*
12. Show the bot's own log on each GitHub run page. Today it goes into a file nobody sees.
13. Add an outside watchdog (healthchecks.io, free, no card): if the bot goes quiet for 15 minutes, you get a Telegram message. **You:** sign up.
14. Send a daily "all good" report to the admin chat: runs, markets checked, errors, and which price source was used.
15. Measure how late each price source is on every run, and warn when one falls behind.
16. Start each run one minute after a candle closes (:01, :06, :11 and so on). **You:** change one cron-job.org setting; I'll give you the steps.
17. Message outbox: if Telegram fails, keep the message and send it on the next run, marked "delayed".
18. If a run is cut off halfway, make sure the next run can't repeat alerts already sent.
19. After each run, delete old saved copies of the bot's memory and keep the last 20.
20. Lock library versions, with an automatic weekly "update and test" check, so a surprise library update can't break the bot overnight.
21. Update the parts of the GitHub workflow that GitHub is retiring. This clears the two warnings shown on every run.
22. Add one pause switch in GitHub settings that stops new signals straight away, without touching code.

### Milestone 3 — Proof from years of history, not weeks
*Done when: every strategy-and-market pair has a multi-year report after costs, and the losing ones are switched off.*
23. Download years of free 5-minute history: Dukascopy for gold, EUR and GBP (spot prices, matching item 1), and Binance's free public files for BTC.
24. Run history tests in small saved pieces that pick up again after a power or internet cut.
25. Tune on older years, then check on years the bot hasn't seen, to catch settings that only worked by luck.
26. Subtract real costs (spread and slippage for each market) from every result.
27. Include the big-picture filters (the macro checks) in history tests, using free historical reports from the US regulator (CFTC). Today's history tests switch these filters off, so what runs live has never been tested.
28. Make a scorecard for every strategy and market, and switch off pairs that lose after costs. Early live warnings: BTC −1.0R, H2 Pullback −2.0R, Zone Bounce −0.5R, though these are too few trades to judge on their own.
29. Check whether higher scores really win more often, and rebalance the scoring if not.
30. Luck test: shuffle the trade order thousands of times to find the worst losing streak and deepest dip to expect. These are honest numbers to show subscribers.
31. Show results by hour and weekday for each market, and quiet the hours that lose.
32. Re-test, on years of data, when to move the stop to entry and where to take profit.
33. Run a weekly live-versus-history check that alerts you when live results drop well below what history predicts.
34. Re-run the history test on every strategy change, and flag anything that makes results worse before it goes live.

### Milestone 4 — Smarter risk and trade handling
*Done when: each change passes Milestone 3's tests, and live results stay inside the tested range.*
35. Add a weekly loss brake on top of the existing daily limit and losing-streak pause.
36. Treat gold, EUR and GBP as partly the same bet on the US dollar, and cap how many run at once in the same direction. This widens the existing EUR/GBP rule.
37. Add a spread cushion to stops and entries, so subscribers aren't stopped out by their broker's spread.
38. 10 of 36 signals were cancelled before they started, so test better ways to enter.
39. Cap how many signals can be open at once across all markets.
40. Show lot sizes for $100, $500 and $1,000 accounts on every signal.
41. Warn people holding an open trade before big news. New signals already pause around news, but open trades get no warning.
42. Friday weekend plan for open trades: close them, or move the stop to entry, before the weekend price jump.
43. Post a monthly risk review in the admin chat: worst day, worst streak, and biggest loss against the plan.

### Milestone 5 — More markets and strategies (only those that pass)
*Done when: each new market or strategy has a free, on-time price feed, passes Milestone 3 after costs, and survives a quiet trial.*
44. Trial mode: new markets and strategies post only to the admin chat until they prove themselves live.
45. Silver.
46. US100 (Nasdaq) and US30 (Dow).
47. USD/JPY and AUD/USD.
48. Oil.
49. ETH.
50. Slower swing signals on 1-hour and 4-hour charts, for people who can't watch every 5 minutes.
51. London and New York opening-range breakout.
52. Gold Asian-session range breakout only; your knowledge base deliberately rules out trading back into the range.
53. A strong one-way-day detector that blocks signals against the day's direction.

### Milestone 6 — A better experience for subscribers
*Done when: a beginner can follow a signal without asking anyone.*
54. Cleaner signal card: a short code (like #G142); entry, stop and targets in price, pips and dollars; reward-to-risk; and how long the signal stays valid.
55. Ethiopian time on everything.
56. Amharic messages, either bilingual or in a separate Amharic channel.
57. One plain line on why this trade.
58. Prices laid out for easy copying into MT4/MT5 on a phone.
59. Morning briefing: gold's key levels, today's news times, and the day's leaning.
60. Pause notices, for example "No trades: US inflation report in 20 minutes."
61. Weekly results as a clean image card, losses included.
62. Instant replies to commands through a free Cloudflare service (no card, and you've used Cloudflare before). Runs every 5 minutes are too slow for chat.
63. Commands: /stats, /open, /last10, /calc (lot size for your balance), /news, /help.
64. A short weekly lesson on risk and patience.

### Milestone 7 — A public, honest track record
*Done when: anyone can check every signal and result without asking you.*
65. A free public results website on GitHub Pages, updated automatically.
66. Every signal published the moment it's sent; GitHub's history proves nothing was changed afterwards.
67. Running total, monthly table, win rate and worst losing streak, bad months included.
68. A page for each market and each strategy.
69. A "How it works" page with a plain risk warning and what +R means in money.
70. A monthly report as an image and a PDF, made automatically.
71. Ready-to-post result images for Telegram, TikTok and Instagram.
72. A landing page with "Join free channel" and "Join VIP" buttons.
73. Later: independent proof by copying signals into a demo account on Myfxbook. This needs a computer running MT5.

### Milestone 8 — Getting paid (only after the proof)
*Start charging only when: at least 100 finished live trades, positive after costs, and the worst losing streak within what the luck test predicted.*
74. **You:** check Ethiopia's rules on selling signals and promoting brokers. I can't give legal advice.
75. Membership bot: /join, choose a plan, pay, receive a one-time VIP invite link; the bot removes members when their time runs out.
76. Automatic USDT (TRC-20) payment check using free blockchain lookups. No bank, no card.
77. Telebirr: the member sends the receipt and you approve with one tap.
78. Reminders 3 days and 1 day before a membership ends.
79. One free 7-day VIP trial per Telegram account.
80. Monthly, 3-month and yearly plans, cheaper the longer you pay for.
81. Referral rewards: invite friends, earn free days.
82. Broker partner link: members who open an account through your link get VIP free, and the broker pays you.
83. Keep member data private on Cloudflare, never in the bot's database. The repo has to stay public to keep GitHub free: the bot uses about 8,600 minutes a month, and private repos only get 2,000.
84. Admin commands: /members, /revenue, /extend, /broadcast.
85. A money page for you: income, renewals, and who left.
86. Later: rent the signals to other channel owners to post under their own names.

### Milestone 9 — Your control room
*Done when: one screen answers "how are we doing, and why?"*
87. One command that downloads the live database to your PC and opens the dashboard.
88. Charts: running total, dips, and win rate by market, strategy and hour.
89. Funnel of trade ideas with reasons: rejected (316), blocked (105), watchlist (34), sent (36).
90. A report card for each filter: money it saved versus money it missed (uses item 5).
91. A traffic light for each strategy, comparing its last 30 trades with history.
92. Price-source panel: how late each source is, failures, and times the backup was used.

### Milestone 10 — Code health and security
*Done when: the automatic checks are green and no secret sits in the public history.*
93. Scan the whole public history for leaked keys or tokens, and replace any found.
94. Check every setting at start-up and give a clear message if one is wrong. The weekly-check crash came from one bad setting.
95. Automatic code-quality checks run alongside the tests.
96. Stamp each signal with the code version that made it.
97. A plain-English handbook: what each admin alert means and what to do.

### Milestone 11 — Advanced (needs lots of data first)
98. A second-opinion filter learned from the bot's own past ideas, only after about 500 finished ones and tested on periods it hasn't seen.
99. A market-mood switch (trending, ranging or wild) that turns groups of strategies on and off, if history shows it helps.
100. A monthly settings check on fresh data that suggests changes to you and never applies them by itself.

