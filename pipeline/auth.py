"""Accounts, sessions, per-user API tokens, and secret encryption (Phase 4).

Passwords: bcrypt. API tokens: random 256-bit, stored as sha256 hex — lookup
is by exact hash, so no timing side-channel on comparison. Gmail OAuth tokens:
Fernet-encrypted at rest with a key derived from TRACKER_SECRET_KEY (§11).
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
from cryptography.fernet import Fernet, InvalidToken

from . import config

SESSION_DAYS = 30


# ---------------------------------------------------------------- passwords

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def check_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


# ---------------------------------------------------------------- API tokens

def new_api_token() -> tuple[str, str]:
    """Returns (plaintext_token, hash). The plaintext is shown ONCE."""
    token = "trk_" + secrets.token_urlsafe(32)
    return token, hash_token(token)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def user_id_for_token(conn, supplied: str | None):
    if not supplied:
        return None
    row = conn.execute("SELECT id FROM users WHERE api_token_hash = %s",
                       (hash_token(supplied),)).fetchone()
    return row["id"] if row else None


# ---------------------------------------------------------------- sessions

def create_session(conn, user_id) -> str:
    row = conn.execute(
        "INSERT INTO sessions (user_id, expires_at) VALUES (%s, %s) RETURNING id",
        (user_id, datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)),
    ).fetchone()
    return str(row["id"])


def session_user(conn, session_id: str | None):
    if not session_id:
        return None
    try:
        return conn.execute(
            """
            SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id
            WHERE s.id = %s::uuid AND s.expires_at > now()
            """, (session_id,)).fetchone()
    except Exception:
        return None


def delete_session(conn, session_id: str | None) -> None:
    if session_id:
        try:
            conn.execute("DELETE FROM sessions WHERE id = %s::uuid", (session_id,))
        except Exception:
            pass


# ---------------------------------------------------------------- encryption

def _fernet() -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(config.SECRET_KEY.encode()).digest())
    return Fernet(key)


def encrypt(text: str) -> str:
    return _fernet().encrypt(text.encode()).decode()


def decrypt(token: str) -> str | None:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        return None
