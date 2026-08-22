from __future__ import annotations

import json
import os
import sqlite3
import time
from collections import deque
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from flask import Flask, flash, redirect, render_template, request, url_for
from flask_login import LoginManager, current_user, login_required, login_user, logout_user

from config.instruments import active_symbols, get_instrument, state_key
from config.settings import BASE_DIR, DB_PATH
from src.dashboard.auth import AdminUser, DEFAULT_USERNAME, load_user, verify_credentials

# Realized R by closing status (kept in sync with the calibration script).
_STATUS_R = {
    "CLOSED_TP2": 2.25,
    "CLOSED_BE": 0.75,
    "CLOSED_SL": -1.0,
    "CLOSED_TIME": 0.0,
    "CLOSED_STRUCT": 1.0,
}


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _fmt_price(value: Any, decimals: int) -> str:
    try:
        return f"{float(value):,.{decimals}f}"
    except (TypeError, ValueError):
        return "—"


@contextmanager
def _db_connection() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _query_value(query: str, params: tuple[Any, ...] = (), default: Any = None) -> Any:
    try:
        with _db_connection() as conn:
            row = conn.execute(query, params).fetchone()
        if row is None:
            return default
        if isinstance(row, sqlite3.Row):
            return row[0]
        return row[0]
    except sqlite3.Error:
        return default


def _query_rows(query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    try:
        with _db_connection() as conn:
            rows = conn.execute(query, params).fetchall()
    except sqlite3.Error:
        return []

    output: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, sqlite3.Row):
            output.append(dict(row))
        else:
            output.append(dict(row))
    return output


def _read_last_lines(path: Path, line_count: int = 100) -> list[str]:
    if not path.exists():
        return [f"File not found: {path}"]

    with path.open("r", encoding="utf-8", errors="replace") as handle:
        lines = deque(handle, maxlen=line_count)
    return [line.rstrip("\n") for line in lines]


def _safe_next_url() -> str:
    next_url = request.args.get("next", "")
    if next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    return url_for("index")


def _format_unix_ts(value: Any) -> str:
    timestamp = _to_int(value, default=0)
    if timestamp <= 0:
        return "N/A"
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(timestamp))


def _symbol_cards() -> list[dict[str, Any]]:
    """Per-market snapshot for the mission-control home page: last price,
    24h change, inline sparkline points, state and setup readouts."""
    cards: list[dict[str, Any]] = []
    now = int(time.time())
    for sym in active_symbols():
        instrument = get_instrument(sym)
        rows = _query_rows(
            """
            SELECT timestamp, close FROM market_data
            WHERE symbol = ? ORDER BY timestamp DESC LIMIT 288;
            """,
            (sym,),
        )
        rows.reverse()
        closes = [float(r["close"]) for r in rows if r.get("close") is not None]
        last_ts = _to_int(rows[-1]["timestamp"]) if rows else 0

        change_pct = None
        if len(closes) >= 2 and closes[0]:
            change_pct = (closes[-1] - closes[0]) / closes[0] * 100.0

        spark = ""
        if len(closes) >= 2:
            step = max(1, len(closes) // 48)
            pts = closes[::step]
            lo, hi = min(pts), max(pts)
            span = (hi - lo) or 1.0
            n = len(pts)
            spark = " ".join(
                f"{(i / (n - 1)) * 100:.1f},{28 - ((v - lo) / span) * 26:.1f}"
                for i, v in enumerate(pts)
            )

        setup_class = _query_value(
            "SELECT value FROM kv_store WHERE key = ?;",
            (state_key("latest_setup_classification", sym),),
            default=None,
        )
        setup_score = _query_value(
            "SELECT value FROM kv_store WHERE key = ?;",
            (state_key("latest_setup_score", sym),),
            default=None,
        )
        structure = _query_value(
            "SELECT value FROM kv_store WHERE key = ?;",
            (state_key("current_structure_state", sym),),
            default=None,
        )

        cards.append(
            {
                "symbol": sym,
                "name": instrument.display_name,
                "asset_class": instrument.asset_class,
                "price": (
                    f"{closes[-1]:,.{instrument.price_decimals}f}" if closes else "—"
                ),
                "change_pct": round(change_pct, 2) if change_pct is not None else None,
                "up": (change_pct or 0.0) >= 0.0,
                "spark": spark,
                "fresh_min": ((now - last_ts) // 60) if last_ts else None,
                "weekend": instrument.weekend_trading,
                "setup_class": str(setup_class) if setup_class else "—",
                "setup_score": str(setup_score) if setup_score else "",
                "structure": str(structure).upper() if structure else "—",
            }
        )
    return cards


def create_app() -> Flask:
    template_dir = os.path.join(BASE_DIR, "src", "dashboard", "templates")
    static_dir = os.path.join(BASE_DIR, "src", "dashboard", "static")
    flask_app = Flask(
        __name__,
        template_folder=template_dir,
        static_folder=static_dir,
        static_url_path="/static",
    )

    flask_app.config["SECRET_KEY"] = os.getenv(
        "DASHBOARD_SECRET_KEY",
        "replace-this-secret-in-production",
    )

    login_manager = LoginManager()
    setattr(login_manager, "login_view", "login")
    login_manager.init_app(flask_app)

    @login_manager.user_loader
    def user_loader(user_id: str) -> AdminUser | None:
        return load_user(user_id)

    @flask_app.route("/login", methods=["GET", "POST"])
    def login() -> Any:
        if current_user.is_authenticated:
            return redirect(url_for("index"))

        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            if verify_credentials(username, password):
                login_user(AdminUser(DEFAULT_USERNAME), remember=False)
                return redirect(_safe_next_url())

            flash("Invalid credentials. Access denied.", "error")
            return render_template("login.html"), 401

        return render_template("login.html")

    @flask_app.route("/logout")
    @login_required
    def logout() -> Any:
        logout_user()
        return redirect(url_for("login"))

    @flask_app.route("/")
    @login_required
    def index() -> Any:
        signals_total = _to_int(_query_value("SELECT COUNT(*) FROM signals;", default=0))
        open_signals = _to_int(
            _query_value(
                """
                SELECT COUNT(*) FROM signals
                WHERE status IN ('PENDING', 'ACTIVE', 'PARTIAL_TP1');
                """,
                default=0,
            )
        )
        closed_signals = _to_int(
            _query_value(
                """
                SELECT COUNT(*) FROM signals
                WHERE status IN ('CLOSED_TP2', 'CLOSED_SL', 'CANCELLED');
                """,
                default=0,
            )
        )

        latest_signal_ts = _query_value(
            "SELECT timestamp FROM signals ORDER BY timestamp DESC LIMIT 1;",
            default=0,
        )

        macro_bias = _query_value(
            "SELECT value FROM kv_store WHERE key = 'global_macro_bias';",
            default=None,
        ) or _query_value(
            "SELECT value FROM kv_store WHERE key = 'macro_bias_state';",
            default="BIAS_NEUTRAL",
        )

        setup_classification = _query_value(
            "SELECT value FROM kv_store WHERE key = 'latest_setup_classification';",
            default="N/A",
        )

        recent_signals = _query_rows(
            """
            SELECT id, signal_hash, symbol, COALESCE(signal_type, type) AS direction, score, status, timestamp
            FROM signals
            ORDER BY id DESC
            LIMIT 8;
            """
        )

        # Net realized R across all closed trades (the one number that matters).
        status_counts = _query_rows(
            "SELECT status, COUNT(*) AS n FROM signals GROUP BY status;"
        )
        net_r = 0.0
        for row in status_counts:
            net_r += _STATUS_R.get(str(row.get("status", "")).upper(), 0.0) * _to_int(
                row.get("n")
            )

        # Open positions with instrument-correct price formatting.
        open_rows = _query_rows(
            """
            SELECT signal_hash, symbol,
                   COALESCE(signal_type, type) AS direction,
                   COALESCE(entry_price, entry) AS entry_price,
                   COALESCE(sl_price, sl) AS sl_price,
                   COALESCE(tp2_price, tp2) AS tp2_price,
                   score, status, timestamp,
                   COALESCE(strategy,'') AS strategy,
                   COALESCE(mfe_r, 0) AS mfe_r
            FROM signals
            WHERE status IN ('PENDING', 'ACTIVE', 'PARTIAL_TP1')
            ORDER BY id DESC;
            """
        )
        open_positions = []
        for row in open_rows:
            sym = str(row.get("symbol") or "XAUUSD")
            nd = get_instrument(sym).price_decimals
            open_positions.append(
                {
                    "hash": row.get("signal_hash"),
                    "symbol": sym,
                    "direction": row.get("direction"),
                    "entry": _fmt_price(row.get("entry_price"), nd),
                    "sl": _fmt_price(row.get("sl_price"), nd),
                    "tp2": _fmt_price(row.get("tp2_price"), nd),
                    "score": row.get("score"),
                    "status": row.get("status"),
                    "strategy": str(row.get("strategy") or "").replace("_", " ").title(),
                    "mfe": round(float(row.get("mfe_r") or 0.0), 2),
                    "when": _format_unix_ts(row.get("timestamp")),
                }
            )

        # Heartbeat: how long since the engine last pulsed?
        last_pulse = _to_int(
            _query_value(
                "SELECT value FROM kv_store WHERE key = 'last_pulse_wallclock';",
                default=0,
            )
        )
        heartbeat_age_min = int((time.time() - last_pulse) // 60) if last_pulse > 0 else None
        heartbeat_state = "UNKNOWN"
        if heartbeat_age_min is not None:
            heartbeat_state = "LIVE" if heartbeat_age_min <= 15 else "STALLED"

        # Detection funnel: what the brain considered, not just what it published.
        recent_setups = _query_rows(
            """
            SELECT symbol, strategy, direction, order_type, score,
                   classification, vetoes, timestamp
            FROM setup_log ORDER BY id DESC LIMIT 12;
            """
        )
        for setup in recent_setups:
            setup["when"] = _format_unix_ts(setup.get("timestamp"))
        funnel_counts = {"REJECTED": 0, "WATCHLIST": 0, "ACTIONABLE": 0}
        for row in _query_rows(
            "SELECT classification, COUNT(*) AS n FROM setup_log "
            "WHERE timestamp >= ? GROUP BY classification;",
            (int(time.time()) - 7 * 86400,),
        ):
            key = str(row.get("classification", "")).upper()
            if key in funnel_counts:
                funnel_counts[key] = _to_int(row.get("n"))

        return render_template(
            "index.html",
            signals_total=signals_total,
            open_signals=open_signals,
            closed_signals=closed_signals,
            macro_bias=str(macro_bias),
            setup_classification=str(setup_classification),
            latest_signal_time=_format_unix_ts(latest_signal_ts),
            recent_signals=recent_signals,
            heartbeat_state=heartbeat_state,
            heartbeat_age_min=heartbeat_age_min,
            symbol_cards=_symbol_cards(),
            net_r=round(net_r, 2),
            open_positions=open_positions,
            recent_setups=recent_setups,
            funnel_counts=funnel_counts,
        )

    @flask_app.route("/api/summary")
    @login_required
    def api_summary() -> Any:
        last_pulse = _to_int(
            _query_value(
                "SELECT value FROM kv_store WHERE key = 'last_pulse_wallclock';",
                default=0,
            )
        )
        status_counts = _query_rows(
            "SELECT status, COUNT(*) AS n FROM signals GROUP BY status;"
        )
        net_r = sum(
            _STATUS_R.get(str(row.get("status", "")).upper(), 0.0) * _to_int(row.get("n"))
            for row in status_counts
        )
        return {
            "heartbeat_age_min": (
                int((time.time() - last_pulse) // 60) if last_pulse else None
            ),
            "net_r": round(net_r, 2),
            "open_signals": _to_int(
                _query_value(
                    "SELECT COUNT(*) FROM signals "
                    "WHERE status IN ('PENDING','ACTIVE','PARTIAL_TP1');",
                    default=0,
                )
            ),
            "markets": _symbol_cards(),
        }

    @flask_app.route("/signals")
    @login_required
    def signals() -> Any:
        symbol_filter = str(request.args.get("symbol", "")).upper().strip()
        status_filter = str(request.args.get("status", "")).upper().strip()
        clauses: list[str] = []
        params: list[Any] = []
        if symbol_filter:
            clauses.append("symbol = ?")
            params.append(symbol_filter)
        if status_filter == "OPEN":
            clauses.append("status IN ('PENDING','ACTIVE','PARTIAL_TP1')")
        elif status_filter == "CLOSED":
            clauses.append("status LIKE 'CLOSED%'")
        elif status_filter:
            clauses.append("status = ?")
            params.append(status_filter)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = _query_rows(
            f"""
            SELECT
                id,
                signal_hash,
                symbol,
                COALESCE(signal_type, type) AS direction,
                COALESCE(entry_price, entry) AS entry_price,
                COALESCE(sl_price, sl) AS sl_price,
                COALESCE(tp1_price, tp1) AS tp1_price,
                COALESCE(tp2_price, tp2) AS tp2_price,
                score,
                status,
                timestamp,
                closure_reason
            FROM signals
            {where}
            ORDER BY id DESC;
            """,
            tuple(params),
        )
        return render_template(
            "signals.html",
            rows=rows,
            all_symbols=active_symbols(),
            symbol_filter=symbol_filter,
            status_filter=status_filter,
        )

    @flask_app.route("/signals.csv")
    @login_required
    def signals_csv() -> Any:
        import csv
        import io

        from flask import Response

        rows = _query_rows(
            """
            SELECT id, signal_hash, symbol,
                   COALESCE(signal_type, type) AS direction,
                   COALESCE(order_type,'LIMIT') AS order_type,
                   COALESCE(strategy,'') AS strategy,
                   COALESCE(entry_price, entry) AS entry_price,
                   COALESCE(sl_price, sl) AS sl_price,
                   COALESCE(tp1_price, tp1) AS tp1_price,
                   COALESCE(tp2_price, tp2) AS tp2_price,
                   score, status, timestamp,
                   COALESCE(mfe_r,0) AS mfe_r, COALESCE(mae_r,0) AS mae_r,
                   COALESCE(closure_reason,'') AS closure_reason
            FROM signals ORDER BY id ASC;
            """
        )
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            [
                "id", "hash", "symbol", "direction", "order_type", "strategy",
                "entry", "sl", "tp1", "tp2", "score", "status",
                "timestamp_utc", "realized_r", "mfe_r", "mae_r", "closure_reason",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row.get("id"), row.get("signal_hash"), row.get("symbol"),
                    row.get("direction"), row.get("order_type"), row.get("strategy"),
                    row.get("entry_price"), row.get("sl_price"),
                    row.get("tp1_price"), row.get("tp2_price"),
                    row.get("score"), row.get("status"),
                    _format_unix_ts(row.get("timestamp")),
                    _STATUS_R.get(str(row.get("status", "")).upper(), ""),
                    row.get("mfe_r"), row.get("mae_r"), row.get("closure_reason"),
                ]
            )
        return Response(
            buffer.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=trade_journal.csv"},
        )

    def _load_signal(signal_hash: str) -> dict[str, Any] | None:
        rows = _query_rows(
            """
            SELECT id, signal_hash, symbol,
                   COALESCE(signal_type, type) AS signal_type,
                   COALESCE(entry_price, entry) AS entry_price,
                   COALESCE(sl_price, sl) AS sl_price,
                   COALESCE(tp1_price, tp1) AS tp1_price,
                   COALESCE(tp2_price, tp2) AS tp2_price,
                   score, status, timestamp, reasoning, closure_reason,
                   COALESCE(order_type,'LIMIT') AS order_type, strategy,
                   COALESCE(mfe_r, 0) AS mfe_r, COALESCE(mae_r, 0) AS mae_r
            FROM signals WHERE signal_hash = ? LIMIT 1;
            """,
            (signal_hash,),
        )
        return rows[0] if rows else None

    @flask_app.route("/signals/<signal_hash>")
    @login_required
    def signal_detail(signal_hash: str) -> Any:
        row = _load_signal(signal_hash)
        if row is None:
            flash("Signal not found.", "error")
            return redirect(url_for("signals"))
        row["when"] = _format_unix_ts(row.get("timestamp"))
        return render_template("signal_detail.html", s=row)

    @flask_app.route("/signals/<signal_hash>/chart.png")
    @login_required
    def signal_chart(signal_hash: str) -> Any:
        from flask import Response

        from src.alerting.chart_renderer import ChartRenderer
        from src.domain.candle import Candle

        row = _load_signal(signal_hash)
        if row is None:
            return Response("not found", status=404)

        ts = _to_int(row.get("timestamp"))
        chart_symbol = str(row.get("symbol") or "XAUUSD")
        candle_rows = _query_rows(
            """
            SELECT symbol, timeframe, timestamp, open, high, low, close, volume
            FROM market_data
            WHERE symbol = ? AND timestamp <= ?
            ORDER BY timestamp DESC LIMIT 60;
            """,
            (chart_symbol, ts if ts > 0 else int(time.time())),
        )
        candles = [
            Candle(
                symbol=c["symbol"], timeframe=c["timeframe"], timestamp=int(c["timestamp"]),
                open=float(c["open"]), high=float(c["high"]), low=float(c["low"]),
                close=float(c["close"]), volume=float(c["volume"] or 0),
            )
            for c in reversed(candle_rows)
        ]

        class _SignalView:
            pass

        view = _SignalView()
        for key in (
            "symbol", "signal_type", "entry_price", "sl_price",
            "tp1_price", "tp2_price", "score", "order_type", "strategy",
        ):
            setattr(view, key, row.get(key))

        png = ChartRenderer().render_signal_chart(candles, view, zone=None)
        if png is None:
            return Response("chart unavailable (not enough stored candles)", status=404)
        return Response(png, mimetype="image/png")

    @flask_app.route("/logs")
    @login_required
    def logs() -> Any:
        daily_log_path = Path(BASE_DIR) / "logs" / "daily-run.log"
        telemetry_path = Path(BASE_DIR) / "logs" / "telemetry.jsonl"

        daily_lines = _read_last_lines(daily_log_path, line_count=100)
        telemetry_lines = _read_last_lines(telemetry_path, line_count=100)

        parsed_telemetry: list[dict[str, Any]] = []
        for line in telemetry_lines:
            try:
                parsed_telemetry.append(json.loads(line))
            except json.JSONDecodeError:
                continue

        return render_template(
            "logs.html",
            daily_lines=daily_lines,
            telemetry_lines=telemetry_lines,
            parsed_telemetry=parsed_telemetry,
        )

    @flask_app.route("/config", methods=["GET", "POST"])
    @login_required
    def config() -> Any:
        if request.method == "POST":
            key = request.form.get("key", "").strip()
            value = request.form.get("value", "").strip()
            if not key:
                flash("Key is required.", "error")
            else:
                try:
                    with _db_connection() as conn:
                        conn.execute(
                            """
                            INSERT INTO kv_store (key, value, updated_at)
                            VALUES (?, ?, strftime('%s','now'))
                            ON CONFLICT(key) DO UPDATE SET
                                value = excluded.value,
                                updated_at = excluded.updated_at;
                            """,
                            (key, value),
                        )
                        conn.commit()
                    flash(f"Updated {key}.", "success")
                    return redirect(url_for("config"))
                except sqlite3.Error:
                    flash("Database write failed. Please retry.", "error")

        kv_rows = _query_rows(
            """
            SELECT key, value, updated_at
            FROM kv_store
            ORDER BY key ASC;
            """
        )
        return render_template("config.html", kv_rows=kv_rows)

    def _set_kv(key: str, value: str) -> bool:
        try:
            with _db_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO kv_store (key, value, updated_at)
                    VALUES (?, ?, strftime('%s','now'))
                    ON CONFLICT(key) DO UPDATE SET
                        value = excluded.value,
                        updated_at = excluded.updated_at;
                    """,
                    (key, str(value)),
                )
                conn.commit()
            return True
        except sqlite3.Error:
            return False

    def _get_kv(key: str, default: Any = None) -> Any:
        return _query_value(
            "SELECT value FROM kv_store WHERE key = ?;", (key,), default=default
        )

    @flask_app.route("/performance")
    @login_required
    def performance() -> Any:
        from scripts.calibrate_from_history import analyze

        symbol_filter = str(request.args.get("symbol", "")).upper().strip()
        where_symbol = "AND symbol = ?" if symbol_filter else ""
        params: tuple[Any, ...] = (symbol_filter,) if symbol_filter else ()

        # Equity curve: cumulative realized R over closed signals, in order.
        closed = _query_rows(
            f"""
            SELECT id, timestamp, status, symbol,
                   COALESCE(strategy,'UNKNOWN') AS strategy
            FROM signals
            WHERE status IN ('CLOSED_TP2','CLOSED_BE','CLOSED_SL','CLOSED_TIME','CLOSED_STRUCT')
            {where_symbol}
            ORDER BY id ASC;
            """,
            params,
        )
        cumulative = 0.0
        curve_labels: list[str] = []
        curve_values: list[float] = []
        for row in closed:
            cumulative += _STATUS_R.get(str(row.get("status", "")).upper(), 0.0)
            curve_labels.append(_format_unix_ts(row.get("timestamp"))[:10])
            curve_values.append(round(cumulative, 2))

        try:
            report = analyze(str(DB_PATH))
        except Exception:
            report = {"strategies": {}, "recommendations": ["Report unavailable."]}

        totals = {
            "closed": len(closed),
            "net_r": round(cumulative, 2),
            "wins": sum(1 for r in closed if str(r.get("status")) == "CLOSED_TP2"),
            "losses": sum(1 for r in closed if str(r.get("status")) == "CLOSED_SL"),
            "breakeven": sum(1 for r in closed if str(r.get("status")) == "CLOSED_BE"),
        }

        excursions = _query_rows(
            f"""
            SELECT COALESCE(strategy,'UNKNOWN') AS strategy,
                   ROUND(AVG(COALESCE(mfe_r,0)),2) AS avg_mfe,
                   ROUND(AVG(COALESCE(mae_r,0)),2) AS avg_mae,
                   COUNT(*) AS n
            FROM signals
            WHERE status LIKE 'CLOSED%' {where_symbol}
            GROUP BY COALESCE(strategy,'UNKNOWN');
            """,
            params,
        )

        # Net R split by killzone/session of signal creation.
        from src.analysis.pivots import current_session_label

        session_r: dict[str, float] = {}
        session_n: dict[str, int] = {}
        for row in closed:
            label = current_session_label(_to_int(row.get("timestamp")))
            r_value = _STATUS_R.get(str(row.get("status", "")).upper(), 0.0)
            session_r[label] = session_r.get(label, 0.0) + r_value
            session_n[label] = session_n.get(label, 0) + 1
        session_split = [
            {"session": label, "net_r": round(value, 2), "n": session_n[label]}
            for label, value in sorted(session_r.items(), key=lambda kv: -kv[1])
        ]

        # Per-symbol scoreboard (net R, trades, win rate).
        symbol_stats: dict[str, dict[str, Any]] = {}
        for row in closed:
            sym = str(row.get("symbol") or "XAUUSD")
            stats = symbol_stats.setdefault(
                sym, {"symbol": sym, "net_r": 0.0, "n": 0, "wins": 0}
            )
            status = str(row.get("status", "")).upper()
            stats["net_r"] += _STATUS_R.get(status, 0.0)
            stats["n"] += 1
            if status == "CLOSED_TP2":
                stats["wins"] += 1
        symbol_split = sorted(
            (
                {
                    "symbol": s["symbol"],
                    "name": get_instrument(s["symbol"]).display_name,
                    "net_r": round(s["net_r"], 2),
                    "n": s["n"],
                    "win_pct": round(100.0 * s["wins"] / s["n"], 1) if s["n"] else 0.0,
                }
                for s in symbol_stats.values()
            ),
            key=lambda item: -item["net_r"],
        )

        # Weekday x NY-hour heatmap of net R (when does the edge really pay?).
        from zoneinfo import ZoneInfo
        from datetime import datetime, timezone as _tz

        ny_tz = ZoneInfo("America/New_York")
        heat: dict[tuple[int, int], float] = {}
        for row in closed:
            ts = _to_int(row.get("timestamp"))
            if ts <= 0:
                continue
            ny_dt = datetime.fromtimestamp(ts, tz=_tz.utc).astimezone(ny_tz)
            key = (ny_dt.weekday(), ny_dt.hour)
            heat[key] = heat.get(key, 0.0) + _STATUS_R.get(
                str(row.get("status", "")).upper(), 0.0
            )
        heat_max = max((abs(v) for v in heat.values()), default=1.0) or 1.0
        weekdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        heatmap = []
        for day_index, day_name in enumerate(weekdays):
            cells = []
            for hour in range(24):
                value = heat.get((day_index, hour))
                intensity = round(abs(value) / heat_max, 2) if value else 0.0
                cells.append(
                    {
                        "value": round(value, 2) if value is not None else None,
                        "positive": (value or 0.0) > 0,
                        "intensity": intensity,
                    }
                )
            heatmap.append({"day": day_name, "cells": cells})

        # R-multiple distribution (outcome histogram).
        outcome_counts: dict[str, int] = {}
        for row in closed:
            status = str(row.get("status", "")).upper()
            outcome_counts[status] = outcome_counts.get(status, 0) + 1
        histogram_order = [
            ("CLOSED_SL", "-1R stop", "neg"),
            ("CLOSED_TIME", "0R time", "flat"),
            ("CLOSED_BE", "+0.75R breakeven", "flat"),
            ("CLOSED_STRUCT", "+1R structure", "pos"),
            ("CLOSED_TP2", "+2.25R full win", "pos"),
        ]
        hist_max = max(outcome_counts.values(), default=1) or 1
        histogram = [
            {
                "label": label,
                "count": outcome_counts.get(status, 0),
                "pct": round(100.0 * outcome_counts.get(status, 0) / hist_max),
                "tone": tone,
            }
            for status, label, tone in histogram_order
        ]

        # Per-market cumulative curves for the overlay chart.
        curve_colors = {
            "XAUUSD": "#e6c174",
            "BTCUSD": "#f7931a",
            "EURUSD": "#49d7ff",
            "GBPUSD": "#b48cf2",
        }
        symbol_curve_map: dict[str, list[float]] = {}
        for row in closed:
            sym = str(row.get("symbol") or "XAUUSD")
            series = symbol_curve_map.setdefault(sym, [])
            previous = series[-1] if series else 0.0
            series.append(
                round(
                    previous
                    + _STATUS_R.get(str(row.get("status", "")).upper(), 0.0),
                    2,
                )
            )
        symbol_curves = [
            {
                "symbol": sym,
                "data": series,
                "color": curve_colors.get(sym, "#94a3b8"),
            }
            for sym, series in symbol_curve_map.items()
            if len(series) >= 2
        ]
        max_curve_len = max((len(c["data"]) for c in symbol_curves), default=0)

        return render_template(
            "performance.html",
            curve_labels=curve_labels,
            curve_values=curve_values,
            strategies=report.get("strategies", {}),
            recommendations=report.get("recommendations", []),
            totals=totals,
            excursions=excursions,
            session_split=session_split,
            symbol_split=symbol_split,
            heatmap=heatmap,
            histogram=histogram,
            all_symbols=active_symbols(),
            symbol_filter=symbol_filter,
            symbol_curves=symbol_curves,
            max_curve_len=max_curve_len,
        )

    @flask_app.route("/risk", methods=["GET"])
    @login_required
    def risk() -> Any:
        now = int(time.time())
        paused = str(_get_kv("trading_paused", "0")) in {"1", "true", "yes"}
        streak = _to_int(_get_kv("risk_consecutive_sl_count", 0))
        last_sl_ts = _to_int(_get_kv("risk_last_sl_timestamp", 0))
        cooldown_left = 0
        if last_sl_ts > 0:
            cooldown_left = max(0, (last_sl_ts + 45 * 60) - now)

        daily_r_date = str(_get_kv("risk_daily_r_date", ""))
        today_key = str(now - (now % 86400))
        daily_r = 0.0
        if daily_r_date == today_key:
            try:
                daily_r = float(_get_kv("risk_daily_r_value", 0.0))
            except (TypeError, ValueError):
                daily_r = 0.0

        news_raw = str(_get_kv("upcoming_news_events_json", "") or "")
        news_events: list[dict[str, Any]] = []
        try:
            parsed = json.loads(news_raw) if news_raw else []
            if isinstance(parsed, list):
                for event in parsed:
                    ts = _to_int(event.get("timestamp") if isinstance(event, dict) else event)
                    if ts > 0:
                        label = event.get("label", "") if isinstance(event, dict) else ""
                        news_events.append(
                            {"timestamp": ts, "label": label, "when": _format_unix_ts(ts)}
                        )
        except (TypeError, ValueError):
            pass

        signals_today = _to_int(
            _query_value(
                "SELECT COUNT(*) FROM signals WHERE COALESCE(timestamp, created_at, 0) >= ?;",
                (int(today_key),),
                default=0,
            )
        )

        return render_template(
            "risk.html",
            paused=paused,
            streak=streak,
            cooldown_left_min=cooldown_left // 60,
            daily_r=round(daily_r, 2),
            news_events=news_events,
            signals_today=signals_today,
            last_sl_time=_format_unix_ts(last_sl_ts) if last_sl_ts else "never",
        )

    @flask_app.route("/risk/toggle-pause", methods=["POST"])
    @login_required
    def toggle_pause() -> Any:
        currently_paused = str(_get_kv("trading_paused", "0")) in {"1", "true", "yes"}
        if _set_kv("trading_paused", "0" if currently_paused else "1"):
            flash(
                "Trading resumed." if currently_paused else "Trading paused (kill switch on).",
                "success",
            )
        else:
            flash("Could not update pause state.", "error")
        return redirect(url_for("risk"))

    @flask_app.route("/risk/news", methods=["POST"])
    @login_required
    def add_news_event() -> Any:
        when = request.form.get("event_time", "").strip()
        label = request.form.get("label", "").strip()
        action = request.form.get("action", "add")

        if action == "clear":
            _set_kv("upcoming_news_events_json", "[]")
            flash("News blackout list cleared.", "success")
            return redirect(url_for("risk"))

        try:
            event_ts = int(time.mktime(time.strptime(when, "%Y-%m-%dT%H:%M"))) - time.timezone
        except (ValueError, OverflowError):
            flash("Invalid date/time format.", "error")
            return redirect(url_for("risk"))

        news_raw = str(_get_kv("upcoming_news_events_json", "") or "[]")
        try:
            events = json.loads(news_raw)
            if not isinstance(events, list):
                events = []
        except (TypeError, ValueError):
            events = []
        events.append({"timestamp": event_ts, "label": label or "high-impact event", "manual": True})
        events = [e for e in events if _to_int(e.get("timestamp") if isinstance(e, dict) else e) > int(time.time()) - 86400]
        if _set_kv("upcoming_news_events_json", json.dumps(events)):
            flash("News blackout window added (UTC).", "success")
        else:
            flash("Could not save news event.", "error")
        return redirect(url_for("risk"))

    @flask_app.route("/market")
    @login_required
    def market() -> Any:
        from src.analysis.pivots import current_session_label

        selected = str(request.args.get("symbol", "XAUUSD")).upper().strip()
        if selected not in set(active_symbols()):
            selected = active_symbols()[0]
        instrument = get_instrument(selected)

        macro_keys = (
            ("macro_regime", "Macro regime (gold vs real yields)"),
            ("global_macro_bias", "Global macro bias"),
            ("global_macro_score", "Macro score"),
            ("macro_cot_state", "COT positioning"),
            ("macro_cot_index", "COT index"),
            ("macro_crisis_mode", "Crisis mode (DXY decoupling)"),
            ("macro_dxy_correlation", "Gold/DXY correlation"),
            ("macro_smt_state", "SMT divergence state"),
            ("macro_smt_z", "SMT z-score"),
            ("macro_fsr_state", "Fundamental shift rate"),
            ("macro_consensus_state", "Consensus surprise state"),
        )
        macro = [
            {"label": label, "value": str(_get_kv(key, "n/a"))}
            for key, label in macro_keys
        ]

        # Per-symbol state (namespaced kv keys; gold keeps legacy names).
        symbol_state = [
            {
                "label": "Market structure",
                "value": str(
                    _get_kv(state_key("current_structure_state", selected), "n/a")
                ),
            },
            {
                "label": "Latest setup score",
                "value": str(_get_kv(state_key("latest_setup_score", selected), "n/a")),
            },
            {
                "label": "Latest setup classification",
                "value": str(
                    _get_kv(state_key("latest_setup_classification", selected), "n/a")
                ),
            },
        ]

        zones = _query_rows(
            """
            SELECT id, type, price_top, price_bottom, status, created_at
            FROM zones
            WHERE status IN ('ACTIVE','UNMITIGATED','MITIGATED')
              AND COALESCE(symbol, 'XAUUSD') = ?
            ORDER BY created_at DESC LIMIT 25;
            """,
            (selected,),
        )
        for zone in zones:
            zone["created"] = _format_unix_ts(zone.get("created_at"))

        sweep_raw = str(
            _get_kv(state_key("latest_liquidity_sweep", selected), "") or ""
        )
        sweep = None
        try:
            parsed_sweep = json.loads(sweep_raw) if sweep_raw else None
            if isinstance(parsed_sweep, dict):
                sweep = {
                    "type": str(parsed_sweep.get("type", "")).replace("_", " ").title(),
                    "when": _format_unix_ts(parsed_sweep.get("timestamp")),
                }
        except (TypeError, ValueError):
            pass

        return render_template(
            "market.html",
            macro=macro,
            zones=zones,
            sweep=sweep,
            session_label=current_session_label(),
            all_symbols=active_symbols(),
            selected_symbol=selected,
            symbol_state=symbol_state,
            instrument_name=instrument.display_name,
            asset_class=instrument.asset_class,
        )

    return flask_app


app = create_app()
