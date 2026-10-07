-- Private member data for the VIP bot (Cloudflare D1, free tier).
-- Apply with: npx wrangler d1 execute gold-members --remote --file=schema.sql

CREATE TABLE IF NOT EXISTS members (
  user_id INTEGER PRIMARY KEY,
  username TEXT,
  first_name TEXT,
  status TEXT NOT NULL DEFAULT 'none',          -- none | trial | active | expired
  plan TEXT,
  expires_at INTEGER,
  trial_used INTEGER NOT NULL DEFAULT 0,
  referred_by INTEGER,
  referral_rewarded INTEGER NOT NULL DEFAULT 0,
  broker_account TEXT,
  reminded3_for INTEGER,                        -- expiry the 3-day reminder was sent for
  reminded1_for INTEGER,                        -- expiry the 1-day reminder was sent for
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  plan TEXT NOT NULL,
  method TEXT NOT NULL,                         -- USDT | TELEBIRR | BROKER
  amount REAL NOT NULL,
  currency TEXT NOT NULL,
  status TEXT NOT NULL,                         -- open | review | paid | rejected | expired
  reference TEXT,                               -- blockchain transaction / receipt note
  created_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,
  decided_at INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS orders_reference ON orders(reference) WHERE reference IS NOT NULL;
CREATE INDEX IF NOT EXISTS orders_open ON orders(status, method);

-- Every day of VIP given and why (payments, trial, referral, broker, owner), plus exits.
CREATE TABLE IF NOT EXISTS grants (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  kind TEXT NOT NULL,                           -- paid | trial | referral | broker | admin | expired
  days INTEGER,
  amount REAL,
  currency TEXT,
  note TEXT,
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS grants_time ON grants(created_at);

-- Messages waiting to be sent (broadcasts go out a few dozen at a time).
CREATE TABLE IF NOT EXISTS outbox (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  chat_id INTEGER NOT NULL,
  text TEXT NOT NULL,
  created_at INTEGER NOT NULL
);
