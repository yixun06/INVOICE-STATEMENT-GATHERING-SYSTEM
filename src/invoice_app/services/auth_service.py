from __future__ import annotations

import base64
import hashlib
import hmac
import os
import tomllib
from pathlib import Path

from ..config import DEFAULT_PASSWORD_HASH, DEFAULT_USERNAME, SECRETS_PATH


def _load_configured_credentials() -> tuple[str, str]:
    username = os.getenv("INV_USERNAME", DEFAULT_USERNAME)
    password_hash = os.getenv("INV_PASSWORD_HASH", DEFAULT_PASSWORD_HASH)

    if SECRETS_PATH.exists():
        try:
            with SECRETS_PATH.open("rb") as handle:
                secrets = tomllib.load(handle)
            app_cfg = secrets.get("app", {})
            if app_cfg.get("username"):
                username = str(app_cfg["username"])
            if app_cfg.get("password_hash"):
                password_hash = str(app_cfg["password_hash"])
        except Exception:
            pass
    return username, password_hash


def verify_password(candidate: str, stored_hash: str) -> bool:
    if not candidate or not stored_hash:
        return False
    if stored_hash.startswith("pbkdf2_sha256$"):
        try:
            _, iterations, salt_b64, expected_b64 = stored_hash.split("$")
            salt = base64.b64decode(salt_b64)
            derived = hashlib.pbkdf2_hmac("sha256", candidate.encode(), salt, int(iterations))
            actual = base64.b64encode(derived).decode()
            return hmac.compare_digest(actual, expected_b64)
        except (ValueError, TypeError):
            return False
    return hmac.compare_digest(candidate, stored_hash)


def authenticate(username: str, password: str) -> bool:
    configured_user, configured_hash = _load_configured_credentials()
    return username == configured_user and verify_password(password, configured_hash)
