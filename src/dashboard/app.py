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

    return flask_app


app = create_app()
