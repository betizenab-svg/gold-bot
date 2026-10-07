# Telegram command and VIP bot (free Cloudflare Worker)

This small program answers Telegram commands instantly (`/stats`, `/open`,
`/last10`, `/calc`, `/news`, `/help`) and runs the VIP membership (plans,
USDT and Telebirr payments, trial, referrals, broker offer, reminders and
removal when time runs out). It runs free on Cloudflare; no card is needed.

The signal engine on GitHub keeps posting signals as before. This bot only
answers people and manages members.

## One-time setup (about 20 steps, all free)

1. Create a free Cloudflare account at https://dash.cloudflare.com/sign-up (email only).
2. Install Node.js on your PC if it is missing (`node --version` should show v20 or newer).
3. Open a terminal in this `cloudflare` folder.
4. Log in: `npx wrangler login` (a browser window opens; allow access).
5. Create the private member database: `npx wrangler d1 create gold-members`.
6. Copy the `database_id` it prints into `wrangler.toml` (replace `REPLACE_WITH_YOUR_DATABASE_ID`).
7. Create the tables: `npx wrangler d1 execute gold-members --remote --file=schema.sql`.
8. Fill in `wrangler.toml` `[vars]`:
   - `BOT_USERNAME`: your bot's name without `@`.
   - `ADMIN_IDS`: your own Telegram user ID (send `/start` to @userinfobot to see it).
   - `ADMIN_CHAT_ID`: the private owner group the bot already reports to.
   - `VIP_CHAT_ID`: the private VIP channel's ID. Add the bot to it as an admin with
     "Invite users" and "Ban users" rights.
   - `USDT_ADDRESS`: your TRON (TRC-20) USDT wallet address (receiving address only).
   - `TELEBIRR_NUMBER` and `TELEBIRR_NAME`: where members send Telebirr payments.
   - `BROKER_LINK`: your broker partner link (leave empty until you have one).
   - `PLANS`: your prices.
   - Leave `SALES_MODE = "auto"`: VIP opens by itself only after the public record has
     100+ finished trades with a positive result after costs.
9. Publish the bot: `npx wrangler deploy`. It prints your bot's address, like
   `https://gold-signals-bot.<your-subdomain>.workers.dev`. Copy that exact address.
10. Store the secrets (you type them; they are never saved in files):
    - `npx wrangler secret put TELEGRAM_BOT_TOKEN`
    - `npx wrangler secret put WEBHOOK_SECRET` (make up a long random password using only
      letters, numbers, `_` and `-`; Telegram refuses other symbols)
    - `npx wrangler secret put ADMIN_PAGE_KEY` (another long random password, for the money page)
    - optional: `npx wrangler secret put TRONGRID_API_KEY` (free key from https://www.trongrid.io)
11. Connect Telegram to the Worker: `./set_webhook.sh <the address from step 9>`
    (it asks for the token and the webhook secret).
12. Test: send `/help` and `/stats` to your bot in Telegram.

## Your money page

Open `<the address from step 9>/admin?key=YOUR_ADMIN_PAGE_KEY`
in a browser. In Telegram you also have `/members`, `/revenue`, `/left`,
`/extend user_id days` and `/broadcast text` (owner only).

## Checks before changing the code

`node --test` in this folder runs the bot's tests (they also run on GitHub
for every change).
