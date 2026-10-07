// Tests for the Cloudflare Worker: node --test cloudflare/
// D1 is stood in for by Node's built-in SQLite; Telegram and TronGrid by a fake fetch.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";
import { test, beforeEach } from "node:test";

import worker, {
  calcText,
  checkUsdt,
  eatTime,
  expireMembers,
  flushOutbox,
  handleUpdate,
  lotFor,
  newsText,
  openText,
  remindMembers,
  revenueText,
  salesGate,
  statsText,
} from "./worker.js";

const schema = readFileSync(new URL("./schema.sql", import.meta.url), "utf8");

function d1(db) {
  return {
    prepare(sql) {
      const statement = db.prepare(sql);
      let params = [];
      const api = {
        bind(...values) {
          params = values;
          return api;
        },
        async first() {
          return statement.get(...params) ?? null;
        },
        async all() {
          return { results: statement.all(...params) };
        },
        async run() {
          const result = statement.run(...params);
          return { meta: { changes: Number(result.changes) } };
        },
      };
      return api;
    },
  };
}

const data = {
  totals: { trades: 120, net_r: 14.5, net_r_after_costs: 9.25, win_rate: 51.2, worst_losing_streak: 6, deepest_dip_r: 5.5 },
  last_30_days: { trades: 20, net_r: 3.25 },
  history: { bad_luck_losing_streak: 9 },
  open: [{ code: "#G142", symbol: "XAUUSD", name: "Gold", direction: "LONG", entry: 4196.4, stop: 4187.86, tp1: 4209.21, tp2: 4222.02, status: "ACTIVE" }],
  last10: [{ code: "#E77", symbol: "EURUSD", name: "Euro", direction: "SHORT", result_r: -1, closed_at: 1791300000 }],
  news: [{ time: Math.floor(Date.now() / 1000) + 3600, label: "US CPI", currency: "USD" }],
  instruments: { XAUUSD: { name: "Gold", pip_size: 0.1, pip_value_per_lot: 10, decimals: 2 } },
};

let calls;
let env;
let tronTransfers;
let unreachableChats;

function fakeFetch(url, init = {}) {
  const href = String(url);
  if (href.startsWith("https://api.telegram.org/")) {
    const method = href.split("/").pop();
    const body = init.body ? JSON.parse(init.body) : {};
    calls.push({ method, body });
    if (unreachableChats.has(String(body.chat_id))) {
      const refused = { ok: false, error_code: 400, description: "Bad Request: chat not found" };
      return Promise.resolve(new Response(JSON.stringify(refused), { status: 400 }));
    }
    const result = method === "createChatInviteLink" ? { invite_link: "https://t.me/+abc" } : { message_id: calls.length };
    return Promise.resolve(new Response(JSON.stringify({ ok: true, result }), { status: 200 }));
  }
  if (href.startsWith("https://api.trongrid.io/")) {
    return Promise.resolve(new Response(JSON.stringify({ data: tronTransfers }), { status: 200 }));
  }
  return Promise.resolve(new Response(JSON.stringify(data), { status: 200 }));
}

beforeEach(() => {
  const db = new DatabaseSync(":memory:");
  db.exec(schema);
  calls = [];
  tronTransfers = [];
  unreachableChats = new Set();
  globalThis.fetch = fakeFetch;
  env = {
    DB: d1(db),
    TELEGRAM_BOT_TOKEN: "t",
    DATA_URL: "https://example.test/public.json",
    BOT_USERNAME: "GoldBot",
    VIP_CHAT_ID: "-100vip",
    ADMIN_CHAT_ID: "-100owner",
    ADMIN_IDS: "999",
    USDT_ADDRESS: "TOwnerWallet",
    TELEBIRR_NUMBER: "0911000000",
    SALES_MODE: "open",
    PLANS: '{"monthly":{"label":"1 month","days":30,"usdt":15,"etb":2000}}',
    WEBHOOK_SECRET: "s3cret",
  };
});

const msg = (from, text, extra = {}) => ({ message: { message_id: 5, chat: { id: from, type: "private" }, from: { id: from, username: `u${from}` }, text, ...extra } });
const sent = (method) => calls.filter((c) => c.method === method);

test("public answers read the track record", () => {
  assert.match(statsText(data, {}), /\+14\.50R.*120 trades/s);
  assert.match(statsText(data, {}), /After typical broker costs: \+9\.25R/);
  assert.match(openText(data), /#G142.*BUY Gold/s);
  assert.match(openText(data), /<code>4196\.40<\/code>/);
  assert.match(newsText(data), /US CPI \(USD\)/);
  assert.match(eatTime(0), /Thu 1 Jan 03:00 EAT/);
  const lot = lotFor(1000, 1, 4196.4, 4187.86, data.instruments.XAUUSD);
  assert.equal(lot.lot, 0.01); // $10 risk on an 85-pip gold stop ($8.54 per 0.01 lot)
  assert.equal(lot.tooBig, false);
  assert.equal(lotFor(10000, 1, 4196.4, 4187.86, data.instruments.XAUUSD).lot, 0.12);
  assert.match(calcText(data, ["100"]), /0\.01 lot.*risks 8\.5%/);
  assert.match(calcText(data, []), /Write your balance/);
});

test("VIP opens only on a proven record (auto mode)", () => {
  assert.equal(salesGate({ SALES_MODE: "auto" }, data).open, true);
  const early = { totals: { trades: 40, net_r: 5, net_r_after_costs: 2 } };
  assert.equal(salesGate({ SALES_MODE: "auto" }, early).open, false);
  const streaky = { ...data, totals: { ...data.totals, worst_losing_streak: 12 } };
  assert.equal(salesGate({ SALES_MODE: "auto" }, streaky).open, false);
  assert.equal(salesGate({ SALES_MODE: "closed" }, data).open, false);
});

test("one free trial per account", async () => {
  await handleUpdate(msg(1, "/trial"), env);
  const row = await env.DB.prepare("SELECT status, trial_used FROM members WHERE user_id = 1").first();
  assert.equal(row.status, "trial");
  assert.equal(row.trial_used, 1);
  assert.equal(sent("createChatInviteLink").length, 1);
  await handleUpdate(msg(1, "/trial"), env);
  assert.match(sent("sendMessage").at(-1).body.text, /once per account/);
});

test("USDT payment is found on the blockchain and the referrer is rewarded", async () => {
  await handleUpdate(msg(7, "/start"), env);
  await handleUpdate(msg(8, "/start ref_7"), env);
  await handleUpdate({ callback_query: { id: "q", data: "pay:monthly:USDT", from: { id: 8 }, message: { chat: { id: 8 } } } }, env);
  const order = await env.DB.prepare("SELECT * FROM orders WHERE user_id = 8").first();
  assert.ok(order.amount > 15 && order.amount < 16);
  tronTransfers = [{
    transaction_id: "tx1", to: "TOwnerWallet", value: String(Math.round(order.amount * 1e6)),
    token_info: { decimals: 6 }, block_timestamp: (order.created_at + 120) * 1000,
  }];
  assert.equal(await checkUsdt(env, fakeFetch), 1);
  const member = await env.DB.prepare("SELECT status FROM members WHERE user_id = 8").first();
  assert.equal(member.status, "active");
  const referrer = await env.DB.prepare("SELECT status, expires_at FROM members WHERE user_id = 7").first();
  assert.equal(referrer.status, "active");
  assert.equal(await checkUsdt(env, fakeFetch), 0); // the same transfer is never used twice
});

test("a short USDT payment goes to the owner instead of being accepted", async () => {
  await handleUpdate({ callback_query: { id: "q", data: "pay:monthly:USDT", from: { id: 9 }, message: { chat: { id: 9 } } } }, env);
  const order = await env.DB.prepare("SELECT * FROM orders WHERE user_id = 9").first();
  tronTransfers = [{
    transaction_id: "tx2", to: "TOwnerWallet", value: String(Math.round((order.amount - 1) * 1e6)),
    token_info: { decimals: 6 }, block_timestamp: (order.created_at + 60) * 1000,
  }];
  assert.equal(await checkUsdt(env, fakeFetch), 0);
  const updated = await env.DB.prepare("SELECT status FROM orders WHERE id = ?").bind(order.id).first();
  assert.equal(updated.status, "review");
  assert.ok(sent("sendMessage").some((c) => c.body.chat_id === "-100owner" && /Payment to check/.test(c.body.text)));
});

test("Telebirr receipt is approved by the owner with one tap", async () => {
  await handleUpdate({ callback_query: { id: "q", data: "pay:monthly:TELEBIRR", from: { id: 5 }, message: { chat: { id: 5 } } } }, env);
  await handleUpdate(msg(5, "", { photo: [{ file_unique_id: "rcpt1", file_id: "F-rcpt1" }] }), env);
  const order = await env.DB.prepare("SELECT * FROM orders WHERE user_id = 5").first();
  assert.equal(order.status, "review");
  // The receipt photo and the Approve/Reject buttons arrive together in the owner group.
  const review = sent("sendPhoto").find((c) => c.body.chat_id === "-100owner");
  assert.equal(review.body.photo, "F-rcpt1");
  assert.match(review.body.caption, /Payment to check/);
  assert.equal(review.body.reply_markup.inline_keyboard[0][0].callback_data, `adm:ok:${order.id}`);
  // Someone who is not the owner cannot approve.
  await handleUpdate({ callback_query: { id: "q", data: `adm:ok:${order.id}`, from: { id: 5 }, message: { chat: { id: 5 } } } }, env);
  assert.equal((await env.DB.prepare("SELECT status FROM orders WHERE id = ?").bind(order.id).first()).status, "review");
  await handleUpdate({ callback_query: { id: "q", data: `adm:ok:${order.id}`, from: { id: 999 }, message: { chat: { id: -100 } } } }, env);
  assert.equal((await env.DB.prepare("SELECT status FROM orders WHERE id = ?").bind(order.id).first()).status, "paid");
  assert.equal((await env.DB.prepare("SELECT status FROM members WHERE user_id = 5").first()).status, "active");
  assert.match(await revenueText(env), /2000\.00 ETB/);
});

test("a receipt sent without an open Telebirr order gets directions, not silence", async () => {
  await handleUpdate(msg(6, "", { photo: [{ file_unique_id: "late1" }] }), env);
  assert.equal(sent("sendPhoto").length, 0);
  assert.match(sent("sendMessage").at(-1).body.text, /tap \/join/);
});

test("if the owner group is unreachable, receipts go to the owner privately", async () => {
  unreachableChats.add("-100owner");
  await handleUpdate({ callback_query: { id: "q", data: "pay:monthly:TELEBIRR", from: { id: 4 }, message: { chat: { id: 4 } } } }, env);
  await handleUpdate(msg(4, "", { photo: [{ file_unique_id: "r4", file_id: "F-r4" }] }), env);
  assert.ok(sent("sendPhoto").some((c) => c.body.chat_id === "999" && c.body.photo === "F-r4"));
});

test("/pending shows every payment waiting for the owner, old and new", async () => {
  await handleUpdate(msg(999, "/pending"), env);
  assert.match(sent("sendMessage").at(-1).body.text, /No payments are waiting/);
  await handleUpdate({ callback_query: { id: "q", data: "pay:monthly:TELEBIRR", from: { id: 3 }, message: { chat: { id: 3 } } } }, env);
  await handleUpdate(msg(3, "", { photo: [{ file_unique_id: "r3", file_id: "F-r3" }] }), env);
  // An order from before photos were kept (as in the live database).
  const now = Math.floor(Date.now() / 1000);
  await env.DB.prepare(
    "INSERT INTO orders (user_id, plan, method, amount, currency, status, reference, created_at, expires_at) " +
      "VALUES (2, 'monthly', 'TELEBIRR', 2000, 'ETB', 'review', 'telebirr:photo:old', ?, ?)",
  ).bind(now, now + 86400).run();
  calls = [];
  await handleUpdate(msg(999, "/pending"), env);
  const photoReview = sent("sendPhoto").find((c) => c.body.chat_id === 999);
  assert.equal(photoReview.body.photo, "F-r3");
  const oldReview = sent("sendMessage").find((c) => /Receipt photo not kept/.test(c.body.text));
  assert.ok(oldReview.body.reply_markup.inline_keyboard[0][0].callback_data.startsWith("adm:ok:"));
  // Members cannot use it.
  calls = [];
  await handleUpdate(msg(3, "/pending"), env);
  assert.equal(calls.length, 0);
});

test("the same receipt photo cannot pay twice", async () => {
  await handleUpdate({ callback_query: { id: "q", data: "pay:monthly:TELEBIRR", from: { id: 5 }, message: { chat: { id: 5 } } } }, env);
  await handleUpdate(msg(5, "", { photo: [{ file_unique_id: "same", file_id: "F-1" }] }), env);
  await handleUpdate({ callback_query: { id: "q", data: "pay:monthly:TELEBIRR", from: { id: 6 }, message: { chat: { id: 6 } } } }, env);
  await handleUpdate(msg(6, "", { photo: [{ file_unique_id: "same", file_id: "F-2" }] }), env);
  assert.match(sent("sendMessage").at(-1).body.text, /already used/);
});

test("reminders, removal when time runs out, and owner broadcasts", async () => {
  const now = Math.floor(Date.now() / 1000);
  env.DB.prepare("INSERT INTO members (user_id, status, expires_at, created_at, updated_at) VALUES (?, 'active', ?, ?, ?)").bind(20, now + 2 * 86400, now, now).run();
  env.DB.prepare("INSERT INTO members (user_id, status, expires_at, created_at, updated_at) VALUES (?, 'active', ?, ?, ?)").bind(21, now - 60, now, now).run();
  assert.equal(await remindMembers(env), 1);
  assert.equal(await remindMembers(env), 0);
  assert.equal(await expireMembers(env), 1);
  assert.equal(sent("banChatMember").length, 1);
  assert.equal(sent("unbanChatMember").length, 1);
  await handleUpdate(msg(999, "/broadcast Market closed tomorrow"), env);
  assert.equal(await flushOutbox(env), 1);
  assert.match(sent("sendMessage").at(-1).body.text, /Market closed tomorrow/);
});

test("webhook calls without the secret are refused", async () => {
  const bad = await worker.fetch(new Request("https://w.test/", { method: "POST", body: "{}" }), env);
  assert.equal(bad.status, 403);
  const good = await worker.fetch(
    new Request("https://w.test/", { method: "POST", body: JSON.stringify(msg(3, "/help")), headers: { "X-Telegram-Bot-Api-Secret-Token": "s3cret" } }),
    env,
  );
  assert.equal(good.status, 200);
  assert.match(sent("sendMessage").at(-1).body.text, /\/stats/);
});
