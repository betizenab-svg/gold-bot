# Free Permanent Hosting (no cPanel needed)

The bot no longer needs a paid server. It runs on **GitHub Actions** — GitHub's
own computers run your bot every 5 minutes, free, forever, with no credit card.
A free outside timer (cron-job.org) starts each run, because GitHub's built-in
timer skips most runs (see "Make it run every 5 minutes" below).

## Why GitHub Actions

| What you need | How GitHub Actions covers it |
|---|---|
| A computer that is always on | GitHub runs the bot on their servers every 5 minutes |
| Free with no card | Free for public repositories, unlimited minutes |
| Survives power cuts at home | Runs in the cloud — your laptop can be off |
| Remembers signals between runs | Each run hands the database to the next one through GitHub's Actions cache, and a copy is saved into the repository every 6 hours |
| Secret bot token stays secret | Stored in GitHub Secrets, never visible in the code |

The bot was redesigned for this: it now trades on 5-minute candles
(`SIGNAL_TIMEFRAME=M5`), which matches the every-5-minutes schedule perfectly.

## One-time setup (about 15 minutes of clicking)

1. **Create a GitHub account** at github.com if you do not have one. Free.

2. **Create a new repository.** Name it anything (example: `gold-bot`).
   Choose **Public** (public repos get unlimited free minutes; private ones
   would run out mid-month). Your Telegram token is NOT in the code, so
   public is safe. The `books/` folder is ignored by git and will never be
   uploaded — do not force-add it (copyrighted material).

3. **Push this project to the repository.** From this folder:

   ```bash
   git remote add github https://github.com/YOUR_USERNAME/gold-bot.git
   git push github YOUR_BRANCH:main
   ```

4. **Add your secrets.** On GitHub: repository → Settings → Secrets and
   variables → Actions → "New repository secret". Add these three:

   | Name | Value |
   |---|---|
   | `TELEGRAM_BOT_TOKEN` | your bot token from @BotFather |
   | `TELEGRAM_CHAT_ID` | your channel/chat id |
   | `TWELVEDATA_API_KEY` | free key from twelvedata.com (backup data source) |

5. **Turn it on.** Repository → Actions tab → enable workflows → open
   "Gold Bot Pulse" → "Run workflow" once to test. Check that a green tick
   appears and your Telegram receives nothing or a signal (no signal is
   normal — most pulses find no setup).

That is all for GitHub. Now set up the outside timer below, otherwise the bot
only runs a few times a day.

## Make it run every 5 minutes (free outside timer)

GitHub's own timer skips most runs: in September 2026 it started the bot only
6-7 times a day instead of 288. Runs started from outside ("Run workflow") are
not skipped, so a free website presses that button every 5 minutes. No card is
needed for either step.

1. **Create a key that can only start the bot.** Sign in to GitHub as the
   account that owns the repository → Settings → Developer settings →
   Personal access tokens → Fine-grained tokens → Generate new token.
   - Name: `pulse-timer`. Expiration: "No expiration" if offered, otherwise
     the longest date allowed (set yourself a reminder to renew it).
   - Repository access: "Only select repositories" → your bot repository.
   - Permissions → Repository permissions → **Actions: Read and write**.
     Nothing else.
   - Generate, then copy the token (it is shown only once).

2. **Create the timer.** Sign up free at cron-job.org → Create cronjob.
   - URL: `https://api.github.com/repos/YOUR_USERNAME/gold-bot/actions/workflows/pulse.yml/dispatches`
   - Schedule: every 5 minutes.
   - Advanced → Request method: **POST**.
   - Advanced → Headers:

     | Key | Value |
     |---|---|
     | `Authorization` | `Bearer` followed by a space and your token |
     | `Accept` | `application/vnd.github+json` |
     | `Content-Type` | `application/json` |
     | `X-GitHub-Api-Version` | `2022-11-28` |

   - Advanced → Request body: `{"ref":"main"}`
   - Turn on "notify me when execution fails", then save.

3. **Check it works.** Within 5 minutes a new "Gold Bot Pulse" run appears in
   the Actions tab, and cron-job.org's history shows status `204` (success).
   `401` means the token is wrong or expired; `404` means the URL or the
   token's repository access is wrong.

## Things to know

- **Timing:** the outside timer starts a run every 5 minutes. GitHub's own
  timer stays on as a backup if the outside timer ever stops, but on its own
  it runs only a few times a day.
- **Keep-alive:** GitHub pauses schedules in repositories with no activity for
  60 days. The bot commits a copy of its database every 6 hours, which counts
  as activity, so it keeps itself alive.
- **Data feed:** Yahoo Finance sometimes rate-limits shared cloud computers.
  The bot already handles this: it retries through free proxies and falls
  back to TwelveData automatically (that is why the third secret matters).
- **Watching it:** The Actions tab shows every run and its log, and
  cron-job.org shows every start. Every signal still goes to Telegram, plus a
  daily status message — if that stops, check both pages.
- **Saved state:** the newest copy of the database lives in the Actions cache
  (Actions → Caches, entries named `bot-state-…`; old ones expire by
  themselves). If GitHub's cache is briefly unavailable, runs skip with a
  warning instead of repeating alerts, and continue once it is back.
- **The dashboard:** The Flask dashboard is not hosted in this setup (Telegram
  is the interface). To view it, run it on your own computer:
  `python -m flask --app src/dashboard/app run` — it reads the database copy
  that `git pull` brings (at most 6 hours old).

## If you ever want a real server later

**Oracle Cloud "Always Free"** gives a permanent free Linux server (Arm: 2
CPUs and 12 GB RAM, or a small AMD one with 1 GB). It requires a credit/debit
card at signup for identity verification (never charged). If you get one:

```bash
git clone <your repo> && cd gold-trading-bot
python3 -m venv venv && venv/bin/pip install -r requirements.txt
cp .env.example .env   # fill in your tokens
crontab -e             # add the line below
*/5 * * * * cd /home/ubuntu/gold-trading-bot && venv/bin/python src/bot_runner.py >> logs/cron.log 2>&1
```

Avoid: Render/Koyeb free tiers (they sleep), Railway/Fly (no longer free),
PythonAnywhere free (only one task per day).
