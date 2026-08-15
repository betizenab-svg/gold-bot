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

from config.settings import BASE_DIR, DB_PATH
from src.dashboard.auth import AdminUser, DEFAULT_USERNAME, load_user, verify_credentials


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


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
            SELECT id, symbol, COALESCE(signal_type, type) AS direction, score, status, timestamp
            FROM signals
            ORDER BY id DESC
            LIMIT 8;
            """
        )

        return render_template(
            "index.html",
            signals_total=signals_total,
            open_signals=open_signals,
            closed_signals=closed_signals,
            macro_bias=str(macro_bias),
            setup_classification=str(setup_classification),
            latest_signal_time=_format_unix_ts(latest_signal_ts),
            recent_signals=recent_signals,
        )

    @flask_app.route("/signals")
    @login_required
    def signals() -> Any:
        rows = _query_rows(
            """
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
            ORDER BY id DESC;
            """
        )
        return render_template("signals.html", rows=rows)

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
        from scripts.calibrate_from_history import OUTCOME_R, analyze

        # Equity curve: cumulative realized R over closed signals, in order.
        closed = _query_rows(
            """
            SELECT id, timestamp, status, COALESCE(strategy,'UNKNOWN') AS strategy
            FROM signals
            WHERE status IN ('CLOSED_TP2','CLOSED_BE','CLOSED_SL','CLOSED_TIME')
            ORDER BY id ASC;
            """
        )
        cumulative = 0.0
        curve_labels: list[str] = []
        curve_values: list[float] = []
        for row in closed:
            cumulative += OUTCOME_R.get(str(row.get("status", "")).upper(), 0.0)
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
            """
            SELECT COALESCE(strategy,'UNKNOWN') AS strategy,
                   ROUND(AVG(COALESCE(mfe_r,0)),2) AS avg_mfe,
                   ROUND(AVG(COALESCE(mae_r,0)),2) AS avg_mae,
                   COUNT(*) AS n
            FROM signals
            WHERE status LIKE 'CLOSED%'
            GROUP BY COALESCE(strategy,'UNKNOWN');
            """
        )

        return render_template(
            "performance.html",
            curve_labels=curve_labels,
            curve_values=curve_values,
            strategies=report.get("strategies", {}),
            recommendations=report.get("recommendations", []),
            totals=totals,
            excursions=excursions,
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
        events.append({"timestamp": event_ts, "label": label or "high-impact event"})
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
            ("current_structure_state", "Market structure"),
            ("latest_setup_score", "Latest setup score"),
            ("latest_setup_classification", "Latest setup classification"),
        )
        macro = [
            {"label": label, "value": str(_get_kv(key, "n/a"))}
            for key, label in macro_keys
        ]

        zones = _query_rows(
            """
            SELECT id, type, price_top, price_bottom, status, created_at
            FROM zones
            WHERE status IN ('ACTIVE','UNMITIGATED','MITIGATED')
            ORDER BY created_at DESC LIMIT 25;
            """
        )
        for zone in zones:
            zone["created"] = _format_unix_ts(zone.get("created_at"))

        sweep_raw = str(_get_kv("latest_liquidity_sweep", "") or "")
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
        )

    return flask_app


app = create_app()
