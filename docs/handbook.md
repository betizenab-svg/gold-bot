# Owner handbook: what each message means and what to do

This guide covers every message the bot sends to **you only** (your admin chat). Each entry says what the message means and what to do. "R" means one unit of risk: −1R is a normal full stop-loss.

---

## Every day

### ✅ Daily check
**What it means:** the bot is alive. It shows yesterday's results, which price sources it used, and how the trial strategies are doing.

**What to do:**
- All markets show the spot feed: nothing to do.
- A market shows "BACKUP futures prices" or "Yahoo" for most of the day: your TwelveData key may be used up or missing. Check the `TWELVEDATA_API_KEY` secret on GitHub.
- A trial line says **"ready to go public"**: see *Promoting a trial* below.
- **No daily check arrived at all:** the bot did not run. Open GitHub → Actions → "Gold Bot Pulse" and look at the newest run.

### 🧪 TRIAL signal (owner only)
**What it means:** a strategy still being tested found a trade. Only you see it, and it does not count in the public record.

**What to do:** nothing. You may watch it to learn how the strategy behaves.

---

## When something needs attention

### ⚠️ Price feed is late / stale
**What it means:** the newest price the bot has is older than it should be. Usually the free price service is slow, or the internet failed on GitHub's side.

**What to do:**
- It clears by itself within an hour: ignore it.
- It repeats all day: check the `TWELVEDATA_API_KEY` secret, and check that the cron-job.org schedule is still switched on.

### 🧪 Strategy auto-quarantine
**What it means:** a strategy lost clearly in real trading (at least 0.25R per trade over many trades), so the bot stopped using it on its own.

**What to do:** nothing urgently. It stays off. To switch it back on later, open the control room, decide whether it deserves another chance, and then delete the `auto_quarantined_strategies` entry from the bot's saved settings (the `kv_store` table). The simplest way is to ask a developer.

### 📉 Live results below history
**What it means:** a strategy is doing clearly worse live than it did in the history tests. It is a warning only; nothing was switched off.

**What to do:** watch the traffic lights in the control room. If it is still red after the next monthly history proof, switch it off by removing it from `PROMOTED_SYMBOLS` / `TRIAL_STRATEGIES`.

### 🛑 Loss brake / pause messages
**What it means:** a safety rule stopped new trades. The rules are:
- daily loss limit
- weekly loss brake
- several stops in a row
- news pause
- weekend close

**What to do:** nothing. They lift by themselves. To pause everything yourself, set the GitHub variable `BOT_PAUSED` to `1`. Set it to `0` to resume.

### ❌ Errors / "pulse failed" (GitHub email or the watchdog)
**What it means:** one run crashed. One failure is normal (for example, a GitHub hiccup). Several in a row are not.

**What to do:**
1. Open GitHub → Actions → the failed run → read the red step.
2. If the error starts with **"Settings problems found"**: a GitHub variable has a typo. The message names the variable and the allowed values. Fix the variable and the next run works.
3. Anything else: copy the red text and ask for help.

---

## Every week and month

### 📊 Weekly report and weekly card
**What it means:** the week's public results, which also go to the free channel.

**What to do:** nothing. Share the picture if you like.

### 📋 Risk review (monthly)
**What it means:** the month's worst day, longest losing streak and biggest single loss.

**What to do:** check that no single loss was much bigger than −1R. If one was, the stop-loss was jumped by a price gap; mention it to a developer.

### 📈 Monthly history proof (GitHub Actions)
**What it means:** the bot re-tests every strategy on fresh history after costs and suggests changes. **It never changes anything by itself.**

**What to do:** read the suggestions in the run summary. Apply one only if it improves results across several months, by changing the matching GitHub variable.

The same monthly run also covers two optional switches, both **off** by default:
- **Market mood** (`MOOD_FILTER`): skips ideas while the market is "wild" (bars much bigger than usual). The proof tests it as `mood_block`. If the report suggests `MOOD_FILTER=block`, you may set that GitHub variable.
- **Second opinion** (`SECOND_OPINION_ENABLED`): a small learning model that skips ideas it thinks are unlikely to win. It needs at least 500 finished ideas first. The report says either "HELPS - you may set SECOND_OPINION_ENABLED=1" or "does not help". Even when switched on, the bot ignores the model unless its latest test says it helps.

---

## Promoting a trial
When the daily check says a trial is **ready to go public**:
1. GitHub → Settings → Secrets and variables → Actions → Variables.
2. Edit `TRIAL_STRATEGIES`: remove that strategy's name. Set it to `NONE` to make every strategy public.
3. From the next run, its signals go to the public channel and count in the public record.

## Looking at everything yourself
Run `.venv/bin/python scripts/control_room.py` on your computer. It downloads the bot's latest saved database (up to 6 hours old) and opens the control room in your browser.

The control room shows:
- the running total and dips;
- results by market, strategy and hour;
- the idea funnel;
- which filters saved or cost money;
- traffic lights;
- the price sources.

It cannot change the live bot.

## Checking settings before you change them
Run `.venv/bin/python -m config.validate`. With bad values it lists each problem in plain words. It also runs automatically when the bot starts and in the GitHub test checks.

## Checking for leaked keys
Run `.venv/bin/python scripts/secret_scan.py --history`. If it finds anything, change that key at the service that issued it. A key that was ever pushed to a public repository must be treated as known to everyone.
