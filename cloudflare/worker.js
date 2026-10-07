/**
 * Gold signals Telegram bot on a free Cloudflare Worker (no card needed).
 *
 * 1. Instant answers to /stats /open /last10 /calc /news /help, read from the
 *    public track record the signal engine publishes on GitHub.
 * 2. VIP membership: plans, USDT (TRC-20) payments checked automatically with
 *    the free TronGrid API, Telebirr receipts approved by the owner with one
 *    tap, one free trial per account, referrals, broker-partner VIP, reminders
 *    and automatic removal when time runs out. Member data stays in a private
 *    Cloudflare D1 database, never in the public repository.
 *
 * Settings live in wrangler.toml ([vars]) and secrets (wrangler secret put).
 */

const USDT_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t";
const EAT_OFFSET = 3 * 3600;
const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

// --- small helpers ---------------------------------------------------------------------

export function nowSec() {
  return Math.floor(Date.now() / 1000);
}

export function esc(value) {
  return String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

const pad = (n) => String(n).padStart(2, "0");

export function eatTime(ts) {
  const d = new Date((Number(ts) + EAT_OFFSET) * 1000);
  return `${DAYS[d.getUTCDay()]} ${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())} EAT`;
}

export function fmtR(value) {
  const n = Number(value || 0);
  return `${n >= 0 ? "+" : ""}${n.toFixed(2)}R`;
}

function fmtPrice(value, decimals) {
  const n = Number(value);
  return Number.isFinite(n) ? n.toFixed(decimals ?? 2) : "?";
}

function isAdmin(env, userId) {
  return adminIds(env).includes(String(userId));
}

function adminIds(env) {
  return String(env.ADMIN_IDS || "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

export function plans(env) {
  try {
    return JSON.parse(env.PLANS || "{}");
  } catch {
    return {};
  }
}

async function tg(env, method, payload) {
  const response = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/${method}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
  let body = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }
  if (!response.ok || !body || !body.ok) {
    console.log(`telegram ${method} failed`, response.status, body && body.description);
    return null;
  }
  return body.result;
}

function say(env, chatId, text, extra = {}) {
  return tg(env, "sendMessage", {
    chat_id: chatId,
    text,
    parse_mode: "HTML",
    disable_web_page_preview: true,
    ...extra,
  });
}

async function getData(env) {
  const response = await fetch(env.DATA_URL, { cf: { cacheTtl: 60, cacheEverything: true } });
  if (!response.ok) throw new Error(`track record unavailable (${response.status})`);
  return response.json();
}

// --- public commands -----------------------------------------------------------------------

export function helpText(env, admin = false) {
  const lines = [
    "<b>Commands</b>",
    "/stats - results so far (every trade, losses included)",
    "/open - signals open right now",
    "/last10 - the last 10 finished trades",
    "/calc 500 - lot sizes for a $500 account (add a risk %, e.g. /calc 500 2)",
    "/challenge 9850 10000 - prop-firm challenge: safe risk and lot sizes (balance now, balance the phase started with)",
    "/news - big news coming up (Ethiopian time)",
    "",
    "<b>VIP</b>",
    "/join - VIP plans and payment",
    "/trial - one free VIP trial",
    "/status - your VIP time left",
    "/refer - invite friends, earn free days",
    "/broker - VIP free through our broker partner",
  ];
  if (admin) {
    lines.push("", "<b>Owner</b>", "/pending  /members  /revenue  /left  /extend user_id days  /broadcast text");
  }
  if (env.SITE_URL) lines.push("", `Full public record: ${env.SITE_URL}`);
  return lines.join("\n");
}

export function statsText(data, env) {
  const total = data.totals || {};
  const month = data.last_30_days || {};
  const lines = [
    "\u{1F4CA} <b>Results so far</b> (every finished trade, losses included)",
    `All time: <b>${fmtR(total.net_r)}</b> from ${total.trades || 0} trades, win rate ${total.win_rate ?? "-"}%`,
  ];
  if (total.net_r_after_costs !== undefined) {
    lines.push(`After typical broker costs: ${fmtR(total.net_r_after_costs)}`);
  }
  lines.push(
    `Worst losing streak: ${total.worst_losing_streak || 0} \u00b7 deepest dip: ${Number(total.deepest_dip_r || 0).toFixed(2)}R`,
    `Last 30 days: ${fmtR(month.net_r)} from ${month.trades || 0} trades`,
    "<i>1R = the amount risked on one trade. With 1% risk, +1R is +1% of the account.</i>",
  );
  if (env.SITE_URL) lines.push(env.SITE_URL);
  return lines.join("\n");
}

export function openText(data) {
  const rows = data.open || [];
  if (!rows.length) return "No open signals right now. New ones are posted in the channel.";
  const lines = ["\u{1F7E2} <b>Open signals</b>"];
  for (const s of rows) {
    const decimals = (data.instruments?.[s.symbol]?.decimals) ?? 2;
    const state = s.status === "PENDING" ? "waiting for entry" : s.status === "PARTIAL_TP1" ? "target 1 hit" : "running";
    const targets = Number(s.tp1) === Number(s.tp2)
      ? `Target <code>${fmtPrice(s.tp2, decimals)}</code> (close the whole trade there)`
      : `Target 1 <code>${fmtPrice(s.tp1, decimals)}</code> \u00b7 Target 2 <code>${fmtPrice(s.tp2, decimals)}</code>`;
    lines.push(
      "",
      `<b>${esc(s.code)}</b> ${s.direction === "LONG" ? "BUY" : "SELL"} ${esc(s.name || s.symbol)} (${state})`,
      `Entry <code>${fmtPrice(s.entry, decimals)}</code> \u00b7 Stop <code>${fmtPrice(s.stop, decimals)}</code>`,
      targets,
    );
  }
  return lines.join("\n");
}

export function last10Text(data) {
  const rows = data.last10 || [];
  if (!rows.length) return "No finished trades yet.";
  const lines = ["\u{1F9FE} <b>Last 10 finished trades</b>"];
  for (const s of rows) {
    lines.push(`${esc(s.code)} ${s.direction === "LONG" ? "BUY" : "SELL"} ${esc(s.name || s.symbol)}: <b>${fmtR(s.result_r)}</b> \u00b7 ${eatTime(s.closed_at)}`);
  }
  return lines.join("\n");
}

export function lotFor(balance, riskPct, entry, stop, instrument) {
  const pips = Math.abs(Number(entry) - Number(stop)) / Number(instrument.pip_size);
  if (!(pips > 0)) return null;
  const raw = (balance * riskPct) / 100 / (pips * Number(instrument.pip_value_per_lot));
  const lot = Math.max(0.01, Math.round(raw * 100) / 100);
  const actual = (100 * lot * pips * Number(instrument.pip_value_per_lot)) / balance;
  return { lot, pips, actual, tooBig: actual > riskPct * 1.5 };
}

export function calcText(data, args) {
  const balance = Number(String(args[0] || "").replace(/[$,]/g, ""));
  const risk = Math.min(5, Math.max(0.1, Number(args[1] || 1)));
  if (!(balance > 0)) return "Write your balance after the command, for example: /calc 500 (or /calc 500 2 for 2% risk).";
  const rows = (data.open || []).filter((s) => data.instruments?.[s.symbol]);
  if (!rows.length) {
    return `No open signals right now. When one is sent, /calc ${balance} shows the lot size for it.`;
  }
  const lines = [`\u{1F4CF} <b>Lot sizes for $${balance.toLocaleString("en-US")} at ${risk}% risk</b>`];
  for (const s of rows) {
    const result = lotFor(balance, risk, s.entry, s.stop, data.instruments[s.symbol]);
    if (!result) continue;
    lines.push(
      `${esc(s.code)} ${esc(s.name || s.symbol)}: <b>${result.lot.toFixed(2)} lot</b>` +
        (result.tooBig ? ` \u26a0 even this risks ${result.actual.toFixed(1)}% - consider skipping` : ""),
    );
  }
  return lines.join("\n");
}

// Challenge ladder (scripts/research/sizing.py): full size only while the account
// is near its start, smaller after losses, so a bad run cannot reach the loss limits.
export function challengeRisk(balance, start) {
  const change = (100 * (balance - start)) / start;
  return { change, risk: change > -2 ? 1.5 : change > -4 ? 1 : 0.5 };
}

export function challengeText(data, args) {
  const balance = Number(String(args[0] || "").replace(/[$,]/g, ""));
  const start = Number(String(args[1] || "").replace(/[$,]/g, ""));
  if (!(balance > 0) || !(start > 0)) {
    return "Write your balance now and the balance this phase started with, for example: /challenge 9850 10000";
  }
  const { change, risk } = challengeRisk(balance, start);
  return [
    `\u{1F3AF} <b>Challenge size</b>: you are ${change >= 0 ? "+" : ""}${change.toFixed(1)}% from the start, so risk <b>${risk}%</b> per trade.`,
    "<i>1.5% while you are less than 2% down, 1% once 2% down, 0.5% once 4% down. After 2 losing trades in a day, stop for that day.</i>",
    "",
    calcText(data, [String(balance), String(risk)]),
  ].join("\n");
}

export function newsText(data) {
  const now = nowSec();
  const rows = (data.news || []).filter((e) => Number(e.time) > now - 900).slice(0, 12);
  if (!rows.length) return "No big news in the coming days.";
  const lines = ["\u{1F4F0} <b>Big news coming up</b> (signals pause 30 min before, 15 min after)"];
  for (const e of rows) lines.push(`${eatTime(e.time)} \u00b7 ${esc(e.label)} (${esc(e.currency)})`);
  return lines.join("\n");
}

// --- VIP membership ---------------------------------------------------------------------------

export function salesGate(env, data) {
  const mode = String(env.SALES_MODE || "auto").toLowerCase();
  if (mode === "open") return { open: true };
  if (mode === "closed") return { open: false, why: "VIP is not open yet." };
  const total = (data && data.totals) || {};
  const net = total.net_r_after_costs ?? total.net_r ?? 0;
  const limit = data?.history?.bad_luck_losing_streak;
  const ready =
    (total.trades || 0) >= 100 &&
    net > 0 &&
    (limit == null || (total.worst_losing_streak || 0) <= limit);
  if (ready) return { open: true };
  return {
    open: false,
    why:
      "VIP opens once the public record shows at least 100 finished trades with a positive result after costs. " +
      `Now: ${total.trades || 0} trades, ${fmtR(net)}.`,
  };
}

async function memberRow(env, user) {
  const now = nowSec();
  await env.DB.prepare(
    "INSERT INTO members (user_id, username, first_name, created_at, updated_at) VALUES (?, ?, ?, ?, ?) " +
      "ON CONFLICT(user_id) DO UPDATE SET username = excluded.username, first_name = excluded.first_name",
  )
    .bind(user.id, user.username || null, user.first_name || null, now, now)
    .run();
  return env.DB.prepare("SELECT * FROM members WHERE user_id = ?").bind(user.id).first();
}

async function inviteLink(env, userId) {
  if (!env.VIP_CHAT_ID) return null;
  const result = await tg(env, "createChatInviteLink", {
    chat_id: env.VIP_CHAT_ID,
    member_limit: 1,
    expire_date: nowSec() + 86400,
    name: `member ${userId}`.slice(0, 32),
  });
  return result ? result.invite_link : null;
}

export async function grant(env, userId, days, kind, extra = {}) {
  const now = nowSec();
  const row = await env.DB.prepare("SELECT status, expires_at FROM members WHERE user_id = ?").bind(userId).first();
  const running = row && ["active", "trial"].includes(row.status) && Number(row.expires_at) > now;
  const expires = (running ? Number(row.expires_at) : now) + Number(days) * 86400;
  const status = kind === "trial" && !(row && row.status === "active" && running) ? "trial" : "active";
  await env.DB.prepare(
    "UPDATE members SET status = ?, plan = COALESCE(?, plan), expires_at = ?, updated_at = ? WHERE user_id = ?",
  )
    .bind(status, extra.plan ?? null, expires, now, userId)
    .run();
  await env.DB.prepare(
    "INSERT INTO grants (user_id, kind, days, amount, currency, note, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
  )
    .bind(userId, kind, days, extra.amount ?? null, extra.currency ?? null, extra.note ?? null, now)
    .run();
  const link = running ? null : await inviteLink(env, userId);
  await say(
    env,
    userId,
    `\u2705 VIP active until <b>${eatTime(expires)}</b>.` +
      (link ? `\nYour personal one-time link to the VIP channel: ${link}` : ""),
  );
  return expires;
}

async function rewardReferrer(env, userId) {
  const row = await env.DB.prepare("SELECT referred_by, referral_rewarded FROM members WHERE user_id = ?")
    .bind(userId)
    .first();
  if (!row || !row.referred_by || row.referral_rewarded) return;
  await env.DB.prepare("UPDATE members SET referral_rewarded = 1 WHERE user_id = ?").bind(userId).run();
  const days = Number(env.REFERRAL_DAYS || 7);
  await grant(env, row.referred_by, days, "referral", { note: `friend ${userId} joined` });
  await say(env, row.referred_by, `\u{1F381} A friend you invited joined VIP: +${days} free days.`);
}

async function markPaid(env, order, reference, received) {
  const now = nowSec();
  const update = await env.DB.prepare(
    "UPDATE orders SET status = 'paid', reference = ?, decided_at = ? WHERE id = ? AND status IN ('open', 'review')",
  )
    .bind(reference, now, order.id)
    .run();
  if (!update.meta || update.meta.changes !== 1) return false;
  const plan = plans(env)[order.plan] || {};
  await grant(env, order.user_id, Number(plan.days || 30), order.method === "BROKER" ? "broker" : "paid", {
    plan: order.plan,
    amount: received ?? order.amount,
    currency: order.currency,
    note: reference,
  });
  if (order.method !== "BROKER") await rewardReferrer(env, order.user_id);
  return true;
}

async function sendToReview(env, order, reference, note, photoId = null) {
  await env.DB.prepare("UPDATE orders SET status = 'review', reference = ? WHERE id = ?").bind(reference, order.id).run();
  const member = await env.DB.prepare("SELECT username, first_name FROM members WHERE user_id = ?")
    .bind(order.user_id)
    .first();
  await notifyOwner(env, reviewText(order, member, note), reviewButtons(order), photoId);
}

function reviewText(order, member, note) {
  const who = member ? [member.first_name, member.username ? `@${member.username}` : ""].filter(Boolean).join(" ") : "";
  return (
    `\u{1F9FE} <b>Payment to check</b> (order ${order.id})\nMember: <code>${order.user_id}</code> ${esc(who)}\n` +
    `Plan: ${esc(order.plan)} \u00b7 ${esc(order.method)} ${order.amount} ${esc(order.currency)}\n${esc(note || "")}`
  );
}

function reviewButtons(order) {
  return {
    reply_markup: {
      inline_keyboard: [[
        { text: "\u2705 Approve", callback_data: `adm:ok:${order.id}` },
        { text: "\u274c Reject", callback_data: `adm:no:${order.id}` },
      ]],
    },
  };
}

// To the owner group; if that fails (wrong id, bot removed), to each owner's private chat.
async function notifyOwner(env, text, extra = {}, photoId = null) {
  const send = (chat) =>
    photoId
      ? tg(env, "sendPhoto", { chat_id: chat, photo: photoId, caption: text, parse_mode: "HTML", ...extra })
      : say(env, chat, text, extra);
  if (env.ADMIN_CHAT_ID && (await send(env.ADMIN_CHAT_ID))) return true;
  let delivered = false;
  for (const id of adminIds(env)) delivered = Boolean(await send(id)) || delivered;
  return delivered;
}

function receiptPhotoId(reference) {
  const parts = String(reference || "").split("|");
  return parts.length > 1 ? parts[1] : null;
}

async function cmdPending(env, chatId) {
  const { results } = await env.DB.prepare(
    "SELECT o.*, m.username, m.first_name FROM orders o LEFT JOIN members m ON m.user_id = o.user_id " +
      "WHERE o.status = 'review' ORDER BY o.id LIMIT 20",
  ).all();
  if (!results || !results.length) return say(env, chatId, "No payments are waiting for you.");
  for (const order of results) {
    const photoId = receiptPhotoId(order.reference);
    const sentAt = `Sent ${eatTime(order.created_at)}.`;
    let note = sentAt;
    if (!photoId && String(order.reference || "").startsWith("telebirr:photo:")) {
      note = `${sentAt} Receipt photo not kept: check your Telebirr app for this amount.`;
    } else if (!photoId && order.reference) {
      note = `${sentAt} Receipt: ${order.reference.replace(/^telebirr:/, "")}`;
    }
    const sent = await tg(
      env,
      photoId ? "sendPhoto" : "sendMessage",
      photoId
        ? { chat_id: chatId, photo: photoId, caption: reviewText(order, order, note), parse_mode: "HTML", ...reviewButtons(order) }
        : { chat_id: chatId, text: reviewText(order, order, note), parse_mode: "HTML", ...reviewButtons(order) },
    );
    if (!sent && photoId) await say(env, chatId, reviewText(order, order, note), reviewButtons(order));
  }
}

async function cmdJoin(env, chatId) {
  const data = await getData(env).catch(() => null);
  const gate = salesGate(env, data);
  if (!gate.open) return say(env, chatId, `\u23f3 ${gate.why}\nFree signals continue in the public channel.`);
  const rows = Object.entries(plans(env)).map(([key, plan]) => [
    { text: `${plan.label}: ${plan.usdt} USDT / ${plan.etb} ETB`, callback_data: `plan:${key}` },
  ]);
  if (!rows.length) return say(env, chatId, "VIP plans are not set up yet.");
  return say(env, chatId, "Choose a VIP plan (longer plans cost less per month):", {
    reply_markup: { inline_keyboard: rows },
  });
}

async function startUsdt(env, chatId, userId, key, plan) {
  if (!env.USDT_ADDRESS) return say(env, chatId, "USDT payments are not set up yet.");
  const now = nowSec();
  const open = await env.DB.prepare("SELECT amount FROM orders WHERE method = 'USDT' AND status = 'open' AND expires_at > ?")
    .bind(now)
    .all();
  const taken = new Set((open.results || []).map((r) => Number(r.amount).toFixed(3)));
  let amount = Number(plan.usdt);
  for (let i = 0; i < 100; i++) {
    const candidate = Math.round((Number(plan.usdt) + (1 + Math.floor(Math.random() * 999)) / 1000) * 1000) / 1000;
    if (!taken.has(candidate.toFixed(3))) {
      amount = candidate;
      break;
    }
  }
  await env.DB.prepare("UPDATE orders SET status = 'expired' WHERE user_id = ? AND status = 'open'").bind(userId).run();
  await env.DB.prepare(
    "INSERT INTO orders (user_id, plan, method, amount, currency, status, created_at, expires_at) " +
      "VALUES (?, ?, 'USDT', ?, 'USDT', 'open', ?, ?)",
  )
    .bind(userId, key, amount, now, now + 2 * 3600)
    .run();
  return say(
    env,
    chatId,
    `\u{1F4B5} Send exactly <b>${amount.toFixed(3)} USDT</b> on the <b>TRON (TRC-20)</b> network to:\n` +
      `<code>${esc(env.USDT_ADDRESS)}</code>\n\n` +
      "The exact amount identifies your payment, so do not round it. Sending from an exchange? " +
      "Add its withdrawal fee so that exactly this amount arrives. It is checked automatically " +
      "every few minutes; this order is valid for 2 hours.",
  );
}

async function startTelebirr(env, chatId, userId, key, plan) {
  if (!env.TELEBIRR_NUMBER) return say(env, chatId, "Telebirr payments are not set up yet.");
  const now = nowSec();
  await env.DB.prepare("UPDATE orders SET status = 'expired' WHERE user_id = ? AND status = 'open'").bind(userId).run();
  await env.DB.prepare(
    "INSERT INTO orders (user_id, plan, method, amount, currency, status, created_at, expires_at) " +
      "VALUES (?, ?, 'TELEBIRR', ?, 'ETB', 'open', ?, ?)",
  )
    .bind(userId, key, Number(plan.etb), now, now + 24 * 3600)
    .run();
  return say(
    env,
    chatId,
    `\u{1F4F1} Send <b>${plan.etb} ETB</b> with Telebirr to <b>${esc(env.TELEBIRR_NUMBER)}</b>` +
      (env.TELEBIRR_NAME ? ` (${esc(env.TELEBIRR_NAME)})` : "") +
      ".\nThen send me the receipt here: a photo or the transaction number. The owner checks it and " +
      "you get access as soon as it is approved.",
  );
}

async function cmdTrial(env, chatId, user) {
  const data = await getData(env).catch(() => null);
  const gate = salesGate(env, data);
  if (!gate.open) return say(env, chatId, `\u23f3 ${gate.why}`);
  const row = await memberRow(env, user);
  if (row.trial_used) return say(env, chatId, "The free trial can be used once per account. /join for the plans.");
  await env.DB.prepare("UPDATE members SET trial_used = 1 WHERE user_id = ?").bind(user.id).run();
  return grant(env, user.id, Number(env.TRIAL_DAYS || 7), "trial");
}

async function cmdStatus(env, chatId, user) {
  const row = await memberRow(env, user);
  if (["active", "trial"].includes(row.status) && Number(row.expires_at) > nowSec()) {
    const left = Math.ceil((Number(row.expires_at) - nowSec()) / 86400);
    return say(env, chatId, `\u2705 VIP ${row.status === "trial" ? "trial " : ""}until <b>${eatTime(row.expires_at)}</b> (${left} day(s) left).`);
  }
  return say(env, chatId, "You have no VIP time right now. /join for the plans or /trial for a free try.");
}

async function cmdRefer(env, chatId, user) {
  await memberRow(env, user);
  const count = await env.DB.prepare("SELECT COUNT(*) AS n, SUM(referral_rewarded) AS paid FROM members WHERE referred_by = ?")
    .bind(user.id)
    .first();
  const link = env.BOT_USERNAME ? `https://t.me/${env.BOT_USERNAME}?start=ref_${user.id}` : "(bot name not set)";
  return say(
    env,
    chatId,
    `\u{1F381} Invite friends: ${link}\nWhen a friend you invited pays for VIP, you get ` +
      `${Number(env.REFERRAL_DAYS || 7)} free days.\nInvited so far: ${count?.n || 0} \u00b7 joined VIP: ${count?.paid || 0}`,
  );
}

async function cmdBroker(env, chatId, user, args) {
  if (!env.BROKER_LINK) return say(env, chatId, "The broker partner offer is not available yet.");
  if (!args.length) {
    return say(
      env,
      chatId,
      `\u{1F91D} Open a trading account through our partner link: ${env.BROKER_LINK}\n` +
        `After your first deposit, send <code>/broker your-account-number</code> here. Once the owner ` +
        `confirms it, you get ${Number(env.BROKER_DAYS || 30)} days of VIP free.`,
    );
  }
  await memberRow(env, user);
  const account = args.join(" ").slice(0, 40);
  const now = nowSec();
  const inserted = await env.DB.prepare(
    "INSERT INTO orders (user_id, plan, method, amount, currency, status, reference, created_at, expires_at) " +
      "VALUES (?, 'broker', 'BROKER', 0, 'USD', 'review', ?, ?, ?) ON CONFLICT DO NOTHING",
  )
    .bind(user.id, `broker:${account}`, now, now + 7 * 86400)
    .run();
  if (!inserted.meta || inserted.meta.changes !== 1) return say(env, chatId, "This account number was already sent.");
  await env.DB.prepare("UPDATE members SET broker_account = ? WHERE user_id = ?").bind(account, user.id).run();
  const order = await env.DB.prepare("SELECT * FROM orders WHERE reference = ?").bind(`broker:${account}`).first();
  await sendToReview(env, order, `broker:${account}`, `Broker account: ${account}`);
  return say(env, chatId, "Thanks! The owner will check it with the broker and let you in.");
}

// --- owner commands -----------------------------------------------------------------------------

async function cmdMembers(env, chatId) {
  const counts = await env.DB.prepare("SELECT status, COUNT(*) AS n FROM members GROUP BY status").all();
  const soon = await env.DB.prepare(
    "SELECT user_id, username, status, expires_at FROM members WHERE status IN ('active', 'trial') ORDER BY expires_at LIMIT 10",
  ).all();
  const lines = ["\u{1F465} <b>Members</b>"];
  for (const row of counts.results || []) lines.push(`${esc(row.status)}: ${row.n}`);
  lines.push("", "<b>Ending soonest</b>");
  for (const row of soon.results || []) {
    lines.push(`<code>${row.user_id}</code> ${esc(row.username ? "@" + row.username : "")} ${esc(row.status)} until ${eatTime(row.expires_at)}`);
  }
  return say(env, chatId, lines.join("\n"));
}

export async function revenueText(env) {
  const now = nowSec();
  const since = now - 120 * 86400;
  const rows = await env.DB.prepare(
    "SELECT strftime('%Y-%m', created_at, 'unixepoch') AS month, currency, SUM(amount) AS total, COUNT(*) AS n " +
      "FROM grants WHERE kind = 'paid' AND created_at >= ? GROUP BY month, currency ORDER BY month DESC",
  )
    .bind(since)
    .all();
  const firsts = await env.DB.prepare(
    "SELECT user_id, MIN(created_at) AS first FROM grants WHERE kind = 'paid' GROUP BY user_id",
  ).all();
  const monthStart = now - 30 * 86400;
  let newcomers = 0;
  for (const row of firsts.results || []) if (Number(row.first) >= monthStart) newcomers += 1;
  const payments = await env.DB.prepare("SELECT COUNT(*) AS n FROM grants WHERE kind = 'paid' AND created_at >= ?")
    .bind(monthStart)
    .first();
  const left = await env.DB.prepare("SELECT COUNT(*) AS n FROM grants WHERE kind = 'expired' AND created_at >= ?")
    .bind(monthStart)
    .first();
  const lines = ["\u{1F4B0} <b>Money</b>"];
  for (const row of rows.results || []) {
    lines.push(`${row.month}: ${Number(row.total).toFixed(2)} ${esc(row.currency)} (${row.n} payment(s))`);
  }
  const renewals = Math.max(0, Number(payments?.n || 0) - newcomers);
  lines.push("", `Last 30 days: ${newcomers} new \u00b7 ${renewals} renewal(s) \u00b7 ${left?.n || 0} left`);
  return lines.join("\n");
}

async function cmdLeft(env, chatId) {
  const rows = await env.DB.prepare(
    "SELECT g.user_id, m.username, g.created_at FROM grants g LEFT JOIN members m ON m.user_id = g.user_id " +
      "WHERE g.kind = 'expired' AND g.created_at >= ? ORDER BY g.created_at DESC LIMIT 30",
  )
    .bind(nowSec() - 30 * 86400)
    .all();
  const lines = ["\u{1F6AA} <b>Left in the last 30 days</b>"];
  for (const row of rows.results || []) lines.push(`<code>${row.user_id}</code> ${esc(row.username ? "@" + row.username : "")} ${eatTime(row.created_at)}`);
  if (lines.length === 1) lines.push("Nobody.");
  return say(env, chatId, lines.join("\n"));
}

async function cmdBroadcast(env, chatId, text) {
  if (!text) return say(env, chatId, "Write the message after the command.");
  const now = nowSec();
  const result = await env.DB.prepare(
    "INSERT INTO outbox (chat_id, text, created_at) SELECT user_id, ?, ? FROM members WHERE status IN ('active', 'trial')",
  )
    .bind(text, now)
    .run();
  return say(env, chatId, `Queued for ${result.meta?.changes || 0} member(s); sent a few dozen at a time.`);
}

// --- message routing ------------------------------------------------------------------------------

const MEMBER_COMMANDS = ["/join", "/plans", "/trial", "/status", "/refer", "/broker"];
const OWNER_COMMANDS = ["/pending", "/members", "/revenue", "/left", "/extend", "/broadcast"];

export async function handleUpdate(update, env) {
  if (update.callback_query) return handleCallback(update.callback_query, env);
  const message = update.message;
  if (!message || !message.from) return;
  const chatId = message.chat.id;
  const user = message.from;
  const text = String(message.text || message.caption || "").trim();

  if (text.startsWith("/")) {
    const [rawCommand, ...args] = text.split(/\s+/);
    const command = rawCommand.split("@")[0].toLowerCase();
    const admin = isAdmin(env, user.id);
    switch (command) {
      case "/start": {
        const payload = args[0] || "";
        if (env.DB && message.chat.type === "private") {
          const row = await memberRow(env, user);
          const referrer = Number(payload.replace("ref_", ""));
          if (payload.startsWith("ref_") && referrer && referrer !== user.id && !row.referred_by && row.status === "none") {
            await env.DB.prepare("UPDATE members SET referred_by = ? WHERE user_id = ?").bind(referrer, user.id).run();
          }
          if (payload === "join") return cmdJoin(env, chatId);
        }
        return say(env, chatId, `Welcome! ${helpText(env, admin)}`);
      }
      case "/help":
        return say(env, chatId, helpText(env, admin));
      case "/stats":
      case "/open":
      case "/last10":
      case "/calc":
      case "/challenge":
      case "/news": {
        const data = await getData(env).catch(() => null);
        if (!data) return say(env, chatId, "The track record is not reachable right now; try again in a minute.");
        const reply = {
          "/stats": () => statsText(data, env),
          "/open": () => openText(data),
          "/last10": () => last10Text(data),
          "/calc": () => calcText(data, args),
          "/challenge": () => challengeText(data, args),
          "/news": () => newsText(data),
        }[command]();
        return say(env, chatId, reply);
      }
    }
    if (!env.DB) return;
    const privateChat = message.chat.type === "private";
    if (MEMBER_COMMANDS.includes(command) && !privateChat) {
      const link = env.BOT_USERNAME ? ` https://t.me/${env.BOT_USERNAME}` : "";
      return say(env, chatId, `Please send ${command} to me in a private chat:${link}`);
    }
    switch (command) {
      case "/join":
      case "/plans":
        return cmdJoin(env, chatId);
      case "/trial":
        return cmdTrial(env, chatId, user);
      case "/status":
        return cmdStatus(env, chatId, user);
      case "/refer":
        return cmdRefer(env, chatId, user);
      case "/broker":
        return cmdBroker(env, chatId, user, args);
    }
    if (!admin || !OWNER_COMMANDS.includes(command)) return;
    if (!privateChat && String(chatId) !== String(env.ADMIN_CHAT_ID || "")) {
      return say(env, chatId, "Owner commands work in the owner group or in my private chat.");
    }
    switch (command) {
      case "/pending":
        return cmdPending(env, chatId);
      case "/members":
        return cmdMembers(env, chatId);
      case "/revenue":
        return say(env, chatId, await revenueText(env));
      case "/left":
        return cmdLeft(env, chatId);
      case "/extend": {
        const [target, days] = args.map(Number);
        if (!target || !days) return say(env, chatId, "Use: /extend user_id days");
        await env.DB.prepare("INSERT OR IGNORE INTO members (user_id, created_at, updated_at) VALUES (?, ?, ?)")
          .bind(target, nowSec(), nowSec())
          .run();
        await grant(env, target, days, "admin", { note: `by ${user.id}` });
        return say(env, chatId, `Done: +${days} day(s) for ${target}.`);
      }
      case "/broadcast":
        return cmdBroadcast(env, chatId, text.replace(/^\/broadcast(@\S+)?\s*/i, ""));
    }
    return;
  }

  // A Telebirr receipt (photo or transaction number) for an open order.
  if (env.DB && message.chat.type === "private") {
    const order = await env.DB.prepare(
      "SELECT * FROM orders WHERE user_id = ? AND method = 'TELEBIRR' AND status = 'open' AND expires_at > ? ORDER BY id DESC LIMIT 1",
    )
      .bind(user.id, nowSec())
      .first();
    if (!order) {
      if (message.photo) {
        return say(env, chatId, "To pay for VIP: tap /join, choose a plan and Telebirr, then send this receipt again.");
      }
      return;
    }
    const photo = (message.photo || []).slice(-1)[0];
    const reference = photo ? `telebirr:photo:${photo.file_unique_id}` : `telebirr:${text.slice(0, 60)}`;
    const used = await env.DB.prepare("SELECT 1 FROM orders WHERE reference = ? OR substr(reference, 1, ?) = ?")
      .bind(reference, reference.length + 1, `${reference}|`)
      .first();
    if (used) return say(env, chatId, "This receipt was already used.");
    const stored = photo && photo.file_id ? `${reference}|${photo.file_id}` : reference;
    await sendToReview(env, order, stored, photo ? "" : `Receipt: ${text}`, photo ? photo.file_id : null);
    return say(env, chatId, "Thanks! The owner will check the receipt and let you in.");
  }
}

async function handleCallback(query, env) {
  const data = String(query.data || "");
  const chatId = query.message?.chat?.id;
  const userId = query.from.id;
  await tg(env, "answerCallbackQuery", { callback_query_id: query.id });
  if (!env.DB) return;
  const [kind, a, b] = data.split(":");
  if (kind === "plan") {
    const plan = plans(env)[a];
    if (!plan) return;
    return say(env, chatId, `${esc(plan.label)}: how will you pay?`, {
      reply_markup: {
        inline_keyboard: [[
          { text: `USDT TRC-20 (${plan.usdt}, automatic)`, callback_data: `pay:${a}:USDT` },
          { text: `Telebirr (${plan.etb} ETB)`, callback_data: `pay:${a}:TELEBIRR` },
        ]],
      },
    });
  }
  if (kind === "pay") {
    const plan = plans(env)[a];
    if (!plan) return;
    await memberRow(env, query.from);
    return b === "USDT" ? startUsdt(env, chatId, userId, a, plan) : startTelebirr(env, chatId, userId, a, plan);
  }
  if (kind === "adm" && isAdmin(env, userId)) {
    const order = await env.DB.prepare("SELECT * FROM orders WHERE id = ?").bind(Number(b)).first();
    if (!order || order.status !== "review") return say(env, chatId, `Order ${b} was already handled.`);
    if (a === "ok") {
      const done = await markPaid(env, order, order.reference || `order:${order.id}`, order.amount);
      return say(env, chatId, done ? `\u2705 Order ${order.id} approved.` : `Order ${order.id} was already handled.`);
    }
    await env.DB.prepare("UPDATE orders SET status = 'rejected', decided_at = ? WHERE id = ?").bind(nowSec(), order.id).run();
    await say(env, order.user_id, "Your payment could not be confirmed. Please contact the owner if you think this is a mistake.");
    return say(env, chatId, `\u274c Order ${order.id} rejected.`);
  }
}

// --- every 10 minutes: payments, expiries, reminders, queued messages ---------------------------

function fraction(value) {
  return Math.round((Number(value) % 1) * 1000);
}

export async function checkUsdt(env, fetcher = fetch) {
  if (!env.USDT_ADDRESS) return 0;
  const now = nowSec();
  await env.DB.prepare("UPDATE orders SET status = 'expired' WHERE method = 'USDT' AND status = 'open' AND expires_at < ?")
    .bind(now - 2 * 3600)
    .run();
  const open = (await env.DB.prepare("SELECT * FROM orders WHERE method = 'USDT' AND status = 'open'").all()).results || [];
  if (!open.length) return 0;
  const since = Math.min(...open.map((o) => Number(o.created_at))) * 1000 - 60000;
  const url =
    `https://api.trongrid.io/v1/accounts/${env.USDT_ADDRESS}/transactions/trc20` +
    `?only_to=true&limit=200&contract_address=${USDT_CONTRACT}&min_timestamp=${since}`;
  const response = await fetcher(url, { headers: env.TRONGRID_API_KEY ? { "TRON-PRO-API-KEY": env.TRONGRID_API_KEY } : {} });
  if (!response.ok) {
    console.log("TronGrid", response.status);
    return 0;
  }
  const body = await response.json();
  let matched = 0;
  for (const tx of body.data || []) {
    if (tx.to !== env.USDT_ADDRESS) continue;
    const decimals = Number(tx.token_info?.decimals ?? 6);
    const received = Number(tx.value) / 10 ** decimals;
    const time = Math.floor(Number(tx.block_timestamp) / 1000);
    const used = await env.DB.prepare("SELECT 1 FROM orders WHERE reference = ?").bind(tx.transaction_id).first();
    if (used) continue;
    const exact = open.find((o) => o.status === "open" && Math.abs(Number(o.amount) - received) < 0.0005 && time >= Number(o.created_at) - 60);
    if (exact) {
      if (await markPaid(env, exact, tx.transaction_id, received)) matched += 1;
      exact.status = "paid";
      continue;
    }
    // Same unique cents but a little short: probably an exchange fee. The owner decides.
    const near = open.find(
      (o) => o.status === "open" && fraction(o.amount) === fraction(received) && received < Number(o.amount) &&
        Number(o.amount) - received <= 3 && time >= Number(o.created_at) - 60,
    );
    if (near) {
      await sendToReview(env, near, tx.transaction_id, `Received ${received} USDT instead of ${near.amount} (tx ${tx.transaction_id}).`);
      near.status = "review";
    }
  }
  return matched;
}

export async function expireMembers(env, limit = 5) {
  const now = nowSec();
  const rows = await env.DB.prepare(
    "SELECT user_id FROM members WHERE status IN ('active', 'trial') AND expires_at < ? LIMIT ?",
  )
    .bind(now, limit)
    .all();
  for (const row of rows.results || []) {
    if (env.VIP_CHAT_ID) {
      await tg(env, "banChatMember", { chat_id: env.VIP_CHAT_ID, user_id: row.user_id, until_date: now + 60 });
      await tg(env, "unbanChatMember", { chat_id: env.VIP_CHAT_ID, user_id: row.user_id, only_if_banned: true });
    }
    await env.DB.prepare("UPDATE members SET status = 'expired', updated_at = ? WHERE user_id = ?").bind(now, row.user_id).run();
    await env.DB.prepare("INSERT INTO grants (user_id, kind, days, created_at) VALUES (?, 'expired', 0, ?)").bind(row.user_id, now).run();
    await say(env, row.user_id, "Your VIP time has ended. Thank you for being with us! /join to come back any time.");
  }
  return (rows.results || []).length;
}

export async function remindMembers(env, limit = 5) {
  const now = nowSec();
  let sent = 0;
  for (const [days, column] of [[3, "reminded3_for"], [1, "reminded1_for"]]) {
    const rows = await env.DB.prepare(
      `SELECT user_id, expires_at FROM members WHERE status IN ('active', 'trial') AND expires_at > ? AND expires_at <= ? ` +
        `AND (${column} IS NULL OR ${column} != expires_at) LIMIT ?`,
    )
      .bind(now, now + days * 86400, limit)
      .all();
    for (const row of rows.results || []) {
      await env.DB.prepare(`UPDATE members SET ${column} = ? WHERE user_id = ?`).bind(row.expires_at, row.user_id).run();
      await say(env, row.user_id, `\u23f0 Your VIP ends on <b>${eatTime(row.expires_at)}</b>. /join to renew without a gap.`);
      sent += 1;
    }
  }
  return sent;
}

export async function flushOutbox(env, limit = 10) {
  const rows = await env.DB.prepare("SELECT id, chat_id, text FROM outbox ORDER BY id LIMIT ?").bind(limit).all();
  for (const row of rows.results || []) {
    await env.DB.prepare("DELETE FROM outbox WHERE id = ?").bind(row.id).run();
    await say(env, row.chat_id, row.text);
  }
  return (rows.results || []).length;
}

// --- owner money page --------------------------------------------------------------------------------

function sameSecret(a, b) {
  const x = String(a || "");
  const y = String(b || "");
  if (!x || x.length !== y.length) return false;
  let diff = 0;
  for (let i = 0; i < x.length; i++) diff |= x.charCodeAt(i) ^ y.charCodeAt(i);
  return diff === 0;
}

async function adminPage(env) {
  const revenue = await revenueText(env);
  const recent = await env.DB.prepare(
    "SELECT user_id, kind, days, amount, currency, created_at FROM grants ORDER BY id DESC LIMIT 50",
  ).all();
  const rows = (recent.results || [])
    .map((r) => `<tr><td>${eatTime(r.created_at)}</td><td>${r.user_id}</td><td>${esc(r.kind)}</td><td>${r.days ?? ""}</td><td>${r.amount ?? ""} ${esc(r.currency || "")}</td></tr>`)
    .join("");
  const body = revenue.replace(/<\/?b>/g, "").split("\n").map((line) => `<p>${line}</p>`).join("");
  return new Response(
    `<!doctype html><meta charset="utf-8"><meta name="robots" content="noindex"><title>Money</title>` +
      `<style>body{font-family:system-ui;margin:2rem;max-width:56rem}td,th{padding:.3rem .6rem;border-bottom:1px solid #ddd}</style>` +
      `${body}<h2>Latest changes</h2><table><tr><th>Time</th><th>Member</th><th>Kind</th><th>Days</th><th>Amount</th></tr>${rows}</table>`,
    { headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" } },
  );
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "GET") {
      if (url.pathname === "/admin" && env.DB && sameSecret(url.searchParams.get("key"), env.ADMIN_PAGE_KEY)) {
        return adminPage(env);
      }
      return new Response("ok");
    }
    if (env.WEBHOOK_SECRET && !sameSecret(request.headers.get("X-Telegram-Bot-Api-Secret-Token"), env.WEBHOOK_SECRET)) {
      return new Response("forbidden", { status: 403 });
    }
    let update;
    try {
      update = await request.json();
    } catch {
      return new Response("bad request", { status: 400 });
    }
    try {
      await handleUpdate(update, env);
    } catch (error) {
      console.log("update failed", error && error.stack ? error.stack : error);
    }
    // Always 200: otherwise Telegram keeps re-sending the same update.
    return new Response("ok");
  },

  async scheduled(_event, env, ctx) {
    if (!env.DB) return;
    ctx.waitUntil(
      (async () => {
        await checkUsdt(env).catch((e) => console.log("usdt check failed", e));
        await expireMembers(env).catch((e) => console.log("expiry failed", e));
        await remindMembers(env).catch((e) => console.log("reminders failed", e));
        await flushOutbox(env).catch((e) => console.log("outbox failed", e));
      })(),
    );
  },
};
