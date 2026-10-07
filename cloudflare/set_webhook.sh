#!/usr/bin/env bash
# Point the Telegram bot at your Cloudflare Worker (run once after deploying).
# Usage: ./set_webhook.sh https://gold-signals-bot.YOUR-NAME.workers.dev
# It asks for the bot token and the webhook secret without showing them.
set -euo pipefail
url="${1:?Give the Worker address, e.g. https://gold-signals-bot.you.workers.dev}"
read -rsp "Telegram bot token: " token; echo
read -rsp "Webhook secret (the same one you gave wrangler): " secret; echo
curl -fsS "https://api.telegram.org/bot${token}/setWebhook" \
  --data-urlencode "url=${url}" \
  --data-urlencode "secret_token=${secret}" \
  --data-urlencode 'allowed_updates=["message","callback_query"]'
echo
curl -fsS "https://api.telegram.org/bot${token}/setMyCommands" \
  -H 'content-type: application/json' \
  -d '{"commands":[{"command":"stats","description":"Results so far, losses included"},{"command":"open","description":"Signals open right now"},{"command":"last10","description":"Last 10 finished trades"},{"command":"calc","description":"Lot size for your balance, e.g. /calc 500"},{"command":"news","description":"Big news coming up"},{"command":"join","description":"VIP plans"},{"command":"status","description":"Your VIP time left"},{"command":"help","description":"All commands"}]}'
echo
