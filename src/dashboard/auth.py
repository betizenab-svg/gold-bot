from __future__ import annotations

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

DEFAULT_USERNAME = "Machete"
DEFAULT_PASSWORD = "@Machete1231"
_PASSWORD_HASH = generate_password_hash(DEFAULT_PASSWORD)


class AdminUser(UserMixin):
    def __init__(self, username: str) -> None:
        self.id = username


def verify_credentials(username: str, password: str) -> bool:
    if username != DEFAULT_USERNAME:
        return False
    return check_password_hash(_PASSWORD_HASH, password)


def load_user(user_id: str) -> AdminUser | None:
    if user_id == DEFAULT_USERNAME:
        return AdminUser(DEFAULT_USERNAME)
    return None
