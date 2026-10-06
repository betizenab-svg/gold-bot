"""Dashboard login. Credentials come from the environment (.env), never from code."""

from __future__ import annotations

import hmac
import os

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash


class AdminUser(UserMixin):
    def __init__(self, username: str) -> None:
        self.id = username


def configured_username() -> str:
    return (os.getenv("DASHBOARD_USERNAME") or "").strip()


def _password_hash() -> str | None:
    explicit = (os.getenv("DASHBOARD_PASSWORD_HASH") or "").strip()
    if explicit:
        return explicit
    plain = os.getenv("DASHBOARD_PASSWORD") or ""
    if plain:
        return generate_password_hash(plain)
    return None


def login_configured() -> bool:
    return bool(configured_username()) and _password_hash() is not None


def verify_credentials(username: str, password: str) -> bool:
    expected = configured_username()
    password_hash = _password_hash()
    if not expected or password_hash is None:
        return False
    if not hmac.compare_digest(str(username).encode("utf-8"), expected.encode("utf-8")):
        return False
    return check_password_hash(password_hash, str(password))


def load_user(user_id: str) -> AdminUser | None:
    expected = configured_username()
    if expected and login_configured() and user_id == expected:
        return AdminUser(expected)
    return None
