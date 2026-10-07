# Your task list (step by step)

Everything here is **free**. No card is needed anywhere. Do the parts in order. Each step says where to click and what to type.

GitHub settings live at **github.com/betizenab-svg/gold-bot → Settings → Secrets and variables → Actions**. That page has two tabs: **Secrets**, for private values, and **Variables**, for normal switches.

---

## Part 0: The new Gold 4-hour System (do these now)

The bot now trades **gold only**, using one combined strategy: the Gold 4-hour System. It sends about **2 signals a week**. Each signal has one entry, one stop and one target. Nothing needs switching on.

1. **Remove the old market list, if you made one.** Open the GitHub **Variables** tab. If you see `SYMBOLS`, delete it. `PROMOTED_SYMBOLS` and `TRIAL_STRATEGIES` no longer matter, so you can leave them.
2. **Fix the admin chat number.** Open the **Secrets** tab, edit `TELEGRAM_ADMIN_CHAT_ID`, and paste `-5384019257` exactly, minus sign included.
3. **How to take each signal on your $10,000 Alpha challenge:**
   1. When a signal arrives, send `/calc 10000 1` to the bot. It replies with the lot size for 1% risk, which is $100.
   2. Open the trade at market with that lot size. Type the signal's stop and target into MT5.
   3. Don't move the stop and don't close half. Let the stop or the target close the trade.
   4. When the bot replies **Time Limit** or **Closed Before The Weekend**, close the trade yourself at market.
   5. Take every signal. The tests count on all of them, and skipping some at random usually hurts.
4. **Ask Alpha Capital support one question, and get the answer in writing:** "May I trade signals from my own Telegram bot?" Their rules ban following other people's signals.
5. **Funded-stage news rule.** Once you're funded, Alpha forbids opening or closing a trade from 5 minutes before to 5 minutes after big news. The bot never opens a trade near big news. A stop or target can still be hit during news, though, so ask support how they treat that.
6. **Your choice: free channel.** With about 2 signals a week, "1 free signal a day" means the free channel gets almost every signal. To keep VIP worth paying for, set the **Variable** `FREE_SIGNALS_PER_DAY` to `0`. The free channel then gets each trade's result and the VIP invite. To keep giving free signals, do nothing.

---

## Part 1: Safety first (do these now)

1. **Change your TwelveData key.** An old key (starting `7b8a…`) was saved in the public code history in March, so treat it as known to everyone.
   1. Log in at twelvedata.com and open your API Keys page.
   2. Create a new key and delete the old one.
   3. On GitHub, open the **Secrets** tab, create or update `TWELVEDATA_API_KEY`, and paste the new key.
2. **Change the old dashboard password** anywhere else you used it. It used to be written in the public README.
3. **Set your own dashboard login.** On your computer, open the `.env` file in the project folder and add:
   ```
   DASHBOARD_USERNAME=pick-a-name
   DASHBOARD_PASSWORD=pick-a-long-password
   ```
   Never commit this file; it is already ignored.

## Part 2: Make the bot report to you privately

4. **Admin chat.**
   1. In Telegram, create a private group (for example "Gold bot – owner") and add your bot to it.
   2. Send any message in the group.
   3. Open `https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates` in a browser and copy the `chat` → `id` number exactly as shown, minus sign included. Small groups look like `-5384019257`; channels and big groups start with `-100`. Don't add or remove digits.
   4. On GitHub, add the **Secret** `TELEGRAM_ADMIN_CHAT_ID` with that number.
5. **Watchdog.**
   1. Sign up free at healthchecks.io.
   2. Create a check with period **5 minutes** and grace **10 minutes**, so you hear within 15 minutes if the bot goes quiet. Connect Telegram in its Integrations page.
   3. Copy the ping URL and add it as the **Secret** `HEALTHCHECK_PING_URL`.
6. **Leave the cron-job.org timer as it is** (every 5 minutes). Nothing to change: the bot already gets each new price candle on time.

## Part 3: The public website

7. **Turn on the website.** On GitHub, go to **Settings → Pages → Source** and choose **GitHub Actions**. The site appears at `https://betizenab-svg.github.io/gold-bot/` after the next code change, or when you run the "Website" workflow by hand (Actions → Website → Run workflow).
8. **Add your channel links.**
   1. Edit `site/config.js` on GitHub (click the file, then the pencil).
   2. Fill in `freeChannelUrl` with your public channel link.
   3. Leave `vipUrl` empty until Part 6.

## Part 4: Instant replies to commands (Cloudflare, free)

9. Follow `cloudflare/README.md` from top to bottom:
   1. Create a free Cloudflare account; no card is needed for Workers or D1.
   2. In the `cloudflare` folder, run `npx wrangler login`.
   3. Run `npx wrangler d1 create gold-members`, then paste the printed id into `wrangler.toml`.
   4. Run `npx wrangler d1 execute gold-members --remote --file schema.sql`.
   5. Fill in the placeholders in `wrangler.toml`.
   6. Run `npx wrangler deploy`.
   7. Add the secrets the README lists (`npx wrangler secret put …`).
   8. Run `./set_webhook.sh` to connect Telegram to the Worker.
   9. Test it: send `/help` to the bot and it should answer within seconds.

## Part 5: Wait for proof (no action, just watch)

10. Every morning, read the **✅ Daily check** in your admin chat. `docs/handbook.md` explains every message the bot sends you.
11. **Promote a trial only when the daily check says "ready to go public":**
    1. Open the GitHub **Variables** tab.
    2. Edit `TRIAL_STRATEGIES` and remove that strategy's name (`NONE` makes them all public).
    3. To make a trial market public, add it to `PROMOTED_SYMBOLS` (for example `XAUUSD_H1`).
12. **Monthly history proof:** you get a summary in the admin chat. Change a GitHub variable only when it suggests a setting and you agree. The bot never changes settings by itself. The same applies to the two optional switches, `MOOD_FILTER` and `SECOND_OPINION_ENABLED` (see the handbook).
13. **Control room on your PC:** run `.venv/bin/python scripts/control_room.py`. It shows the bot's numbers from up to 6 hours ago.

## Part 6: Getting paid (only after 100+ finished live trades, positive after costs)

14. **Legal check (required).** Ask a lawyer, or the National Bank of Ethiopia and the Ethiopian Capital Market Authority, whether selling trading signals and promoting a foreign broker is allowed for you. I cannot give legal advice.
15. **Payment details.** In `cloudflare/wrangler.toml`, fill in:
    - `USDT_ADDRESS`: your USDT TRC-20 wallet address (from a wallet you control, for example Trust Wallet);
    - `TELEBIRR_NUMBER` and `TELEBIRR_NAME`;
    - `BROKER_LINK`, if you have a broker partner link;
    - the prices and the VIP channel settings that `cloudflare/README.md` lists.

    Then run `npx wrangler deploy` again.
16. **VIP channel.**
    1. Create a private Telegram channel and add the bot as an **admin** with the "invite users" right.
    2. Get its id the same way as step 4.
    3. On GitHub, add the **Secret** `TELEGRAM_VIP_CHAT_ID`.
    4. Add the **Variable** `VIP_JOIN_URL` with the link to your bot (`https://t.me/<yourbot>`).
    5. Optionally, add the **Variable** `FREE_SIGNALS_PER_DAY` (default 1).
    6. Put the same join link in `site/config.js` → `vipUrl`.
17. **Sales open by themselves.** Leave `SALES_MODE = "auto"` in `wrangler.toml`. The bot only starts selling once the public record meets the rule.

## Part 7: Optional extras

18. **Amharic.** Ask a native speaker to read the Amharic lines in `src/alerting/i18n.py` and correct them. Then set the **Variable** `MESSAGE_LANGUAGE` to `both` (or `am`).
19. **Independent proof (Myfxbook).** On a computer that can stay on, install MT5 with a free demo account and follow `tools/mt5/README.md`. Then connect the demo account to a free Myfxbook account. The copier is untested, so watch it on the demo first.
20. **Renting signals to other channels.** When a partner adds your bot as an admin in their channel, add their channel id to the **Secret** `PARTNER_CHAT_IDS` (comma-separated).

## Good to know

- **The bot's saved database is public.** It is copied into the public repository every 6 hours, and anyone can download it, including the messages it sends to your admin chat. It holds no passwords or keys, and member data lives only on Cloudflare. Don't put anything private in admin messages.
- **A system package was installed on your computer.** While I worked, your terminal's "command not found" helper installed the `sqlite` package. It is harmless, and you can keep or remove it (`sudo dnf remove sqlite`).
- **Typos are caught at start-up.** If a GitHub variable has a typo, the run fails with "Settings problems found" and names the exact variable. Fix it and the next run works.
