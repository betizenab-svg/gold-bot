#!/usr/bin/env bash
# Point the Telegram bot at your Cloudflare Worker (run once after deploying).
# Usage: ./set_webhook.sh <the address "npx wrangler deploy" printed>
# It asks for the bot token and the webhook secret without showing them.
set -euo pipefail
url="${1:?Give the Worker address that npx wrangler deploy printed}"
case "$url" in
  *YOURNAME*|*YOUR-NAME*|*.you.workers.dev*)
    echo "That is the example address. Use the one 'npx wrangler deploy' printed (it ends in .workers.dev)." >&2
    exit 1 ;;
esac
read -rsp "Telegram bot token: " token; echo
read -rsp "Webhook secret (the same one you gave wrangler): " secret; echo
if [[ ! "$secret" =~ ^[A-Za-z0-9_-]{1,256}$ ]]; then
  echo "Telegram only accepts letters, numbers, _ and - in the webhook secret." >&2
  echo "Make a new one, store it with 'npx wrangler secret put WEBHOOK_SECRET', then run this again." >&2
  exit 1
fi

telegram() {
  local method="$1" reply
  shift
  reply=$(curl -sS -m 30 "https://api.telegram.org/bot${token}/${method}" "$@") ||
    { echo "Could not reach Telegram - check the internet and try again." >&2; exit 1; }
  if [[ "$reply" != *'"ok":true'* ]]; then
    echo "Telegram refused ${method}: $(sed -n 's/.*"description":"\([^"]*\)".*/\1/p' <<<"$reply")" >&2
    [[ "$reply" == *Unauthorized* || "$reply" == *'"error_code":404'* ]] && echo "The bot token is wrong - copy it again from @BotFather." >&2
    exit 1
  fi
  echo "${method}: OK"
}

telegram setWebhook \
  --data-urlencode "url=${url}" \
  --data-urlencode "secret_token=${secret}" \
  --data-urlencode 'allowed_updates=["message","callback_query"]'
telegram setMyCommands \
  -H 'content-type: application/json' \
  -d '{"commands":[{"command":"stats","description":"Results so far, losses included"},{"command":"open","description":"Signals open right now"},{"command":"last10","description":"Last 10 finished trades"},{"command":"calc","description":"Lot size for your balance, e.g. /calc 500"},{"command":"news","description":"Big news coming up"},{"command":"join","description":"VIP plans"},{"command":"status","description":"Your VIP time left"},{"command":"help","description":"All commands"}]}'
echo "Done. Send /help to your bot in Telegram to test."
