# Free Permanent Hosting (no cPanel needed)

The bot no longer needs a paid server. It runs on **GitHub Actions** — GitHub's
own computers run your bot every 5 minutes, free, forever, with no credit card.

## Why GitHub Actions

| What you need | How GitHub Actions covers it |
|---|---|
| A computer that is always on | GitHub runs the bot on their servers every 5 minutes |
| Free with no card | Free for public repositories, unlimited minutes |
| Survives power cuts at home | Runs in the cloud — your laptop can be off |
| Remembers signals between runs | The database file is saved back into the repository after every run |
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

That is all. From now on it runs every 5 minutes automatically.

## Things to know

- **Timing:** GitHub schedules are not to-the-second. A "every 5 minutes" job
  sometimes runs 3-10 minutes apart. For 5-minute-candle swing signals this
  does not matter.
- **Keep-alive:** GitHub pauses schedules in repositories with no activity for
  60 days. The bot commits its database after every run, which counts as
  activity, so it keeps itself alive.
- **Data feed:** Yahoo Finance sometimes rate-limits shared cloud computers.
  The bot already handles this: it retries through free proxies and falls
  back to TwelveData automatically (that is why the third secret matters).
- **Watching it:** The Actions tab shows every run and its log. Your signal
  history is in `data/trading_engine.db` in the repository, and every signal
  still goes to Telegram as before.
- **The dashboard:** The Flask dashboard is not hosted in this setup (Telegram
  is the interface). To view it, run it on your own computer:
  `python -m flask --app src/dashboard/app run` — it reads the same database
  file after you `git pull`.

## If you ever want a real server later

**Oracle Cloud "Always Free"** gives a permanent free Linux server (4 ARM CPUs,
24 GB RAM — absurdly generous). It requires a credit/debit card at signup for
identity verification (never charged). If you get one:

```bash
git clone <your repo> && cd gold-trading-bot
python3 -m venv venv && venv/bin/pip install -r requirements.txt
cp .env.example .env   # fill in your tokens
crontab -e             # add the line below
*/5 * * * * cd /home/ubuntu/gold-trading-bot && venv/bin/python src/bot_runner.py >> logs/cron.log 2>&1
```

Avoid: Render/Koyeb free tiers (they sleep), Railway/Fly (no longer free),
PythonAnywhere free (only one task per day).
