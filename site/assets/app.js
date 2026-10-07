// Public results site: everything is computed in the browser from the
// append-only ledger the bot commits to GitHub, so nobody has to trust us.
(function () {
  "use strict";
  const config = window.SITE_CONFIG || {};
  const EAT = 3 * 3600 * 1000;
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const NAMES = {
    XAUUSD: "Gold", BTCUSD: "Bitcoin", EURUSD: "Euro", GBPUSD: "Pound", ETHUSD: "Ethereum",
    US100: "US100 (Nasdaq)", US500: "US500 (S&P 500)", USDJPY: "Dollar/Yen", AUDUSD: "Aussie dollar",
    XAUUSD_H1: "Gold 1-hour swing", XAUUSD_H4: "Gold 4-hour swing", EURUSD_H1: "Euro 1-hour swing",
    GBPUSD_H1: "Pound 1-hour swing",
  };

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const pad = (n) => String(n).padStart(2, "0");
  const fmtR = (v) => `${v >= 0 ? "+" : ""}${Number(v || 0).toFixed(2)}R`;
  const cls = (v) => (v > 0 ? "good" : v < 0 ? "bad" : "");
  const name = (symbol) => NAMES[symbol] || symbol;
  const nice = (strategy) => String(strategy || "Unknown").replace(/_/g, " ").toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase());

  function eat(iso) {
    const d = new Date(new Date(iso).getTime() + EAT);
    return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())} EAT`;
  }

  async function fetchText(file) {
    for (const base of [config.dataBase, "data/"]) {
      if (!base) continue;
      try {
        const response = await fetch(base + file, { cache: "no-store" });
        if (response.ok) return await response.text();
      } catch (error) {
        /* try the next copy */
      }
    }
    return "";
  }

  async function loadLedger() {
    const text = await fetchText("ledger.jsonl");
    return text.split("\n").filter(Boolean).map((line) => ({ line, item: JSON.parse(line) }));
  }

  async function loadPublic() {
    const text = await fetchText("public.json");
    return text ? JSON.parse(text) : {};
  }

  function closedTrades(ledger) {
    return ledger
      .map((row) => row.item)
      .filter((e) => e.event === "CLOSE" && e.result_r !== null && e.result_r !== undefined)
      .map((e) => ({ ...e, result_r: Number(e.result_r), when: new Date(e.event_time) }))
      .sort((a, b) => a.when - b.when);
  }

  function summary(values) {
    const count = values.length;
    let total = 0, peak = 0, dip = 0, streak = 0, worst = 0;
    for (const v of values) {
      total += v;
      peak = Math.max(peak, total);
      dip = Math.min(dip, total - peak);
      streak = v < 0 ? streak + 1 : 0;
      worst = Math.max(worst, streak);
    }
    const wins = values.filter((v) => v > 0).length;
    return {
      trades: count, wins, losses: values.filter((v) => v < 0).length, net: total,
      winRate: count ? (100 * wins) / count : 0, worstStreak: worst, dip: -dip,
    };
  }

  function cards(stats, extra = []) {
    const items = [
      ["Result so far", `<span class="${cls(stats.net)}">${fmtR(stats.net)}</span>`],
      ["Finished trades", stats.trades],
      ["Win rate", stats.trades ? `${stats.winRate.toFixed(0)}%` : "-"],
      ["Worst losing streak", stats.worstStreak],
      ["Deepest dip", `${stats.dip.toFixed(2)}R`],
      ...extra,
    ];
    return `<div class="cards">${items.map(([label, value]) => `<div class="card"><div class="label">${label}</div><div class="value">${value}</div></div>`).join("")}</div>`;
  }

  function drawEquity(canvas, trades) {
    const ratio = window.devicePixelRatio || 1;
    const width = canvas.clientWidth * ratio;
    const height = canvas.clientHeight * ratio;
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext("2d");
    const points = [0];
    for (const t of trades) points.push(points[points.length - 1] + t.result_r);
    const min = Math.min(0, ...points), max = Math.max(0, ...points);
    const span = max - min || 1;
    const x = (i) => 40 * ratio + (i / Math.max(1, points.length - 1)) * (width - 60 * ratio);
    const y = (v) => height - 25 * ratio - ((v - min) / span) * (height - 50 * ratio);
    ctx.strokeStyle = "#475569";
    ctx.lineWidth = ratio;
    ctx.beginPath();
    ctx.moveTo(x(0), y(0));
    ctx.lineTo(x(points.length - 1), y(0));
    ctx.stroke();
    ctx.fillStyle = "#94a3b8";
    ctx.font = `${12 * ratio}px system-ui`;
    ctx.fillText(fmtR(max), 2, y(max) + 4 * ratio);
    ctx.fillText(fmtR(min), 2, y(min));
    ctx.strokeStyle = "#38bdf8";
    ctx.lineWidth = 3 * ratio;
    ctx.beginPath();
    points.forEach((v, i) => (i ? ctx.lineTo(x(i), y(v)) : ctx.moveTo(x(i), y(v))));
    ctx.stroke();
  }

  function groupTable(trades, key, label, linkParam) {
    const groups = {};
    for (const t of trades) (groups[t[key]] = groups[t[key]] || []).push(t.result_r);
    const rows = Object.entries(groups)
      .map(([k, v]) => [k, summary(v)])
      .sort((a, b) => b[1].net - a[1].net)
      .map(([k, s]) => {
        const text = key === "symbol" ? name(k) : nice(k);
        const cell = linkParam ? `<a href="results.html?${linkParam}=${encodeURIComponent(k)}">${esc(text)}</a>` : esc(text);
        return `<tr><td>${cell}</td><td class="num">${s.trades}</td><td class="num ${cls(s.net)}">${fmtR(s.net)}</td><td class="num">${s.winRate.toFixed(0)}%</td><td class="num">${s.worstStreak}</td></tr>`;
      });
    return `<div class="scroll"><table><tr><th>${label}</th><th class="num">Trades</th><th class="num">Result</th><th class="num">Win rate</th><th class="num">Worst streak</th></tr>${rows.join("") || '<tr><td colspan="5" class="muted">No finished trades yet.</td></tr>'}</table></div>`;
  }

  function monthTable(trades) {
    const groups = {};
    for (const t of trades) {
      const d = new Date(t.when.getTime() + EAT);
      const key = `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}`;
      (groups[key] = groups[key] || []).push(t.result_r);
    }
    const rows = Object.keys(groups).sort().reverse().map((month) => {
      const s = summary(groups[month]);
      return `<tr><td>${month}</td><td class="num">${s.trades}</td><td class="num ${cls(s.net)}">${fmtR(s.net)}</td><td class="num">${s.winRate.toFixed(0)}%</td><td class="num">${s.worstStreak}</td></tr>`;
    });
    return `<div class="scroll"><table><tr><th>Month</th><th class="num">Trades</th><th class="num">Result</th><th class="num">Win rate</th><th class="num">Worst streak</th></tr>${rows.join("") || '<tr><td colspan="5" class="muted">No finished trades yet.</td></tr>'}</table></div>`;
  }

  function signalTable(ledger, filter) {
    const opens = {};
    const results = {};
    for (const { item } of ledger) {
      if (item.event === "OPEN") opens[item.id] = item;
      else results[item.id] = item;
    }
    const rows = Object.values(opens)
      .filter(filter)
      .sort((a, b) => b.id - a.id)
      .slice(0, 300)
      .map((o) => {
        const r = results[o.id];
        const outcome = !r ? "open" : r.event === "CANCEL" ? "cancelled (never opened)" : fmtR(r.result_r);
        return `<tr><td>${esc(o.code)}${o.backfilled ? ' <span class="muted">*</span>' : ""}</td><td>${eat(o.signal_time)}</td><td>${esc(name(o.symbol))}</td><td>${o.direction === "LONG" ? "Buy" : "Sell"}</td><td class="num">${esc(o.entry)}</td><td class="num">${esc(o.stop)}</td><td class="num">${esc(o.tp1)} / ${esc(o.tp2)}</td><td class="num ${r && r.event === "CLOSE" ? cls(Number(r.result_r)) : ""}">${outcome}</td></tr>`;
      });
    return `<div class="scroll"><table><tr><th>Code</th><th>Sent</th><th>Market</th><th>Side</th><th class="num">Entry</th><th class="num">Stop</th><th class="num">Targets</th><th class="num">Result</th></tr>${rows.join("") || '<tr><td colspan="8" class="muted">No signals yet.</td></tr>'}</table></div><p class="muted">* recorded from the bot's database when this public record started, not at the moment it was sent.</p>`;
  }

  async function sha256(text) {
    const bytes = new TextEncoder().encode(text);
    const digest = await crypto.subtle.digest("SHA-256", bytes);
    return Array.from(new Uint8Array(digest)).map((b) => b.toString(16).padStart(2, "0")).join("");
  }

  async function verifyChain(ledger) {
    let prev = "genesis";
    for (const { line, item } of ledger) {
      if (item.prev !== prev) return { ok: false, at: item.seq };
      prev = await sha256(line);
    }
    return { ok: true, count: ledger.length };
  }

  function links() {
    for (const el of document.querySelectorAll("[data-link]")) {
      const url = config[el.dataset.link];
      if (url) el.href = url;
      else el.setAttribute("aria-disabled", "true");
    }
    for (const el of document.querySelectorAll("[data-repo-path]")) {
      if (config.repoUrl) el.href = config.repoUrl + el.dataset.repoPath;
    }
  }

  const pages = {
    async home() {
      const ledger = await loadLedger();
      const trades = closedTrades(ledger);
      const stats = summary(trades.map((t) => t.result_r));
      const cutoff = Date.now() - 30 * 86400 * 1000;
      const month = summary(trades.filter((t) => t.when.getTime() >= cutoff).map((t) => t.result_r));
      document.getElementById("stats").innerHTML = cards(stats, [["Last 30 days", `<span class="${cls(month.net)}">${fmtR(month.net)}</span>`]]);
      const latest = trades.slice(-5).reverse().map((t) => `<tr><td>${esc(t.code)}</td><td>${esc(name(t.symbol))}</td><td>${eat(t.event_time)}</td><td class="num ${cls(t.result_r)}">${fmtR(t.result_r)}</td></tr>`);
      document.getElementById("latest").innerHTML = latest.length
        ? `<table><tr><th>Code</th><th>Market</th><th>Finished</th><th class="num">Result</th></tr>${latest.join("")}</table>`
        : '<p class="muted">No finished trades yet.</p>';
    },
    async results() {
      const params = new URLSearchParams(location.search);
      const market = params.get("market");
      const strategy = params.get("strategy");
      const ledger = await loadLedger();
      let trades = closedTrades(ledger);
      if (market) trades = trades.filter((t) => t.symbol === market);
      if (strategy) trades = trades.filter((t) => t.strategy === strategy);
      const title = market ? name(market) : strategy ? nice(strategy) : "All markets and strategies";
      document.getElementById("title").textContent = title;
      const all = closedTrades(ledger);
      const filterLinks = [`<a href="results.html" class="${!market && !strategy ? "active" : ""}">All</a>`]
        .concat([...new Set(all.map((t) => t.symbol))].map((s) => `<a href="results.html?market=${encodeURIComponent(s)}" class="${s === market ? "active" : ""}">${esc(name(s))}</a>`))
        .concat([...new Set(all.map((t) => t.strategy))].map((s) => `<a href="results.html?strategy=${encodeURIComponent(s)}" class="${s === strategy ? "active" : ""}">${esc(nice(s))}</a>`));
      document.getElementById("filters").innerHTML = filterLinks.join("");
      document.getElementById("stats").innerHTML = cards(summary(trades.map((t) => t.result_r)));
      drawEquity(document.getElementById("equity"), trades);
      document.getElementById("months").innerHTML = monthTable(trades);
      document.getElementById("markets").innerHTML = groupTable(trades, "symbol", "Market", "market");
      document.getElementById("strategies").innerHTML = groupTable(trades, "strategy", "Strategy", "strategy");
      document.getElementById("signals").innerHTML = signalTable(
        ledger,
        (o) => (!market || o.symbol === market) && (!strategy || o.strategy === strategy),
      );
    },
    async how() {
      const data = await loadPublic();
      const history = data.history || {};
      const markets = Object.entries(history.markets || {});
      const window_ = history.window || {};
      const rows = markets.map(([symbol, m]) => `<tr><td>${esc(name(symbol))}</td><td class="num">${m.trades}</td><td class="num ${cls(m.net_r_after_costs)}">${fmtR(m.net_r_after_costs)}</td><td>${m.passed ? "passes" : "does not pass"}</td></tr>`);
      document.getElementById("history").innerHTML = markets.length
        ? `<p>Tested on ${esc(window_.from || "?")} to ${esc(window_.to || "?")}, after spread and slippage:</p><div class="scroll"><table><tr><th>Market</th><th class="num">Trades</th><th class="num">Result after costs</th><th>Verdict</th></tr>${rows.join("")}</table></div>` +
          (history.bad_luck_losing_streak ? `<p>Even with nothing wrong, history says a losing streak of up to <b>${history.bad_luck_losing_streak}</b> trades and a dip of up to <b>${history.bad_luck_dip_r}R</b> can happen from bad luck alone.</p>` : "")
        : '<p class="muted">The multi-year history test has not been published yet.</p>';
    },
    async verify() {
      const button = document.getElementById("check");
      button.addEventListener("click", async () => {
        const out = document.getElementById("result");
        out.textContent = "Checking...";
        const ledger = await loadLedger();
        const result = await verifyChain(ledger);
        out.innerHTML = result.ok
          ? `<span class="good">All ${result.count} records are intact: each one carries the fingerprint of the one before it.</span>`
          : `<span class="bad">Record ${result.at} does not match the one before it.</span>`;
      });
    },
    async reports() {
      let list = [];
      try {
        const response = await fetch((config.reportsBase || "reports/") + "index.json", { cache: "no-store" });
        if (response.ok) list = await response.json();
      } catch (error) {
        list = [];
      }
      const base = config.reportsBase || "reports/";
      document.getElementById("reports").innerHTML = list.length
        ? list.slice().reverse().map((month) => `<div class="card"><h3>${esc(month)}</h3><a href="${base}${esc(month)}.pdf">PDF report</a> · <a href="${base}${esc(month)}-square.png">square image</a> · <a href="${base}${esc(month)}-story.png">story image</a><br><img alt="Results for ${esc(month)}" src="${base}${esc(month)}.png" style="max-width:100%;margin-top:.5rem;border-radius:.4rem"></div>`).join("")
        : '<p class="muted">The first monthly report appears on the 1st of next month.</p>';
    },
  };

  links();
  const page = document.body.dataset.page;
  if (pages[page]) pages[page]().catch((error) => {
    console.error(error);
    const box = document.getElementById("error");
    if (box) box.textContent = "The track record could not be loaded right now. Please try again in a minute.";
  });
})();
