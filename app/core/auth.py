"""
User accounts and login sessions for the search UI.

- Passwords are stored as salted scrypt hashes (Python standard library).
- A login creates a random session token; the browser keeps it in an
  HttpOnly cookie, the database only its SHA-256.
- The first account ever created becomes the administrator.
"""

import base64
import hashlib
import hmac
import re
import secrets
import threading
import time

from .config import SESSION_DAYS
from .db import connect, write_lock


ROLES = ("user", "admin")
USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,32}$")
MIN_PASSWORD_LENGTH = 8

_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}

# Failed logins per (ip, username): at most 8 within 10 minutes.
_failures = {}
_failures_lock = threading.Lock()
_MAX_FAILURES = 8
_FAILURE_WINDOW = 600


class AuthError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


# =====================================================
# Passwords
# =====================================================

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **_SCRYPT)
    b64 = lambda b: base64.b64encode(b).decode()
    return f"scrypt${_SCRYPT['n']}${_SCRYPT['r']}${_SCRYPT['p']}${b64(salt)}${b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, digest = stored.split("$")
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(
            password.encode(), salt=base64.b64decode(salt),
            n=int(n), r=int(r), p=int(p), dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def validate_username(username: str) -> str:
    username = (username or "").strip()
    if not USERNAME_RE.match(username):
        raise AuthError("Username: 3-32 characters (letters, digits, dot, dash, underscore).")
    return username


def validate_password(password: str) -> str:
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise AuthError(f"Password: at least {MIN_PASSWORD_LENGTH} characters.")
    return password


# =====================================================
# Users
# =====================================================

def public(user) -> dict:
    return {"id": user["id"], "username": user["username"], "role": user["role"]}


def user_count() -> int:
    with connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def get_user(user_id):
    with connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def get_user_by_name(username):
    with connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    return dict(row) if row else None


def create_user(username, password, role=None) -> dict:
    """Create an account; without an explicit role the first account becomes admin."""
    username = validate_username(username)
    password_hash = hash_password(validate_password(password))
    now = time.time()
    with write_lock, connect() as conn:
        if conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
            raise AuthError("This username is already taken.", 409)
        if role is None:
            role = "admin" if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0 else "user"
        if role not in ROLES:
            raise AuthError("Unknown role.")
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
            (username, password_hash, role, now),
        )
        user_id = cur.lastrowid
    return get_user(user_id)


def _throttle_key(ip, username):
    return (ip or "?", (username or "").strip().lower())


def check_throttle(ip, username):
    now = time.time()
    with _failures_lock:
        recent = [t for t in _failures.get(_throttle_key(ip, username), []) if now - t < _FAILURE_WINDOW]
        _failures[_throttle_key(ip, username)] = recent
        if len(recent) >= _MAX_FAILURES:
            raise AuthError("Too many failed attempts. Please wait a few minutes and try again.", 429)


def _record_failure(ip, username):
    with _failures_lock:
        _failures.setdefault(_throttle_key(ip, username), []).append(time.time())


def authenticate(username, password, ip=None) -> dict:
    """Return the user for valid credentials, otherwise raise AuthError."""
    check_throttle(ip, username)
    user = get_user_by_name((username or "").strip())
    if user is None or not verify_password(password or "", user["password_hash"]):
        _record_failure(ip, username)
        raise AuthError("Invalid username or password.", 401)
    if user["disabled"]:
        raise AuthError("This account has been disabled. Please contact the administrator.", 403)
    with _failures_lock:
        _failures.pop(_throttle_key(ip, username), None)
    return user


def change_password(user_id, current, new):
    user = get_user(user_id)
    if user is None or not verify_password(current or "", user["password_hash"]):
        raise AuthError("The current password is not correct.", 403)
    set_password(user_id, new)


def set_password(user_id, new):
    password_hash = hash_password(validate_password(new))
    with write_lock, connect() as conn:
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id))


def set_disabled(user_id, disabled: bool):
    with write_lock, connect() as conn:
        if disabled:
            _ensure_other_admin(conn, user_id)
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        conn.execute("UPDATE users SET disabled = ? WHERE id = ?", (1 if disabled else 0, user_id))


def set_role(user_id, role):
    if role not in ROLES:
        raise AuthError("Unknown role.")
    with write_lock, connect() as conn:
        if role != "admin":
            _ensure_other_admin(conn, user_id)
        conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))


def _ensure_other_admin(conn, user_id):
    """Refuse to disable or demote the last active administrator."""
    row = conn.execute(
        "SELECT COUNT(*) FROM users WHERE role = 'admin' AND disabled = 0 AND id != ?", (user_id,)
    ).fetchone()
    target = conn.execute("SELECT role FROM users WHERE id = ?", (user_id,)).fetchone()
    if target and target["role"] == "admin" and row[0] == 0:
        raise AuthError("At least one active administrator must remain.", 409)


def list_users():
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT u.id, u.username, u.role, u.disabled, u.created_at, u.last_login_at,
                   (SELECT COUNT(*) FROM conversations c WHERE c.user_id = u.id) AS chats,
                   (SELECT COUNT(*) FROM conversations c WHERE c.user_id = u.id
                        AND c.deleted_at IS NOT NULL) AS deleted_chats,
                   (SELECT COUNT(*) FROM audit_log a WHERE a.user_id = u.id
                        AND a.action = 'ask') AS questions,
                   (SELECT MAX(a.created_at) FROM audit_log a WHERE a.user_id = u.id) AS last_activity
            FROM users u ORDER BY u.created_at
            """
        ).fetchall()
    return [dict(r) for r in rows]


# =====================================================
# Sessions
# =====================================================

def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(user_id, ip=None, user_agent=None) -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    with write_lock, connect() as conn:
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
        conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (now, user_id))
        conn.execute(
            "INSERT INTO sessions (token_hash, user_id, created_at, expires_at, ip, user_agent) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (_token_hash(token), user_id, now, now + SESSION_DAYS * 86400, ip, (user_agent or "")[:300]),
        )
    return token


def user_for_session(token):
    """The logged-in user for a session cookie, or None."""
    if not token:
        return None
    with connect() as conn:
        row = conn.execute(
            "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id "
            "WHERE s.token_hash = ? AND s.expires_at > ? AND u.disabled = 0",
            (_token_hash(token), time.time()),
        ).fetchone()
    return dict(row) if row else None


def delete_session(token):
    if not token:
        return
    with write_lock, connect() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))


def delete_user_sessions(user_id):
    """Sign the user out everywhere (e.g. after a password reset)."""
    with write_lock, connect() as conn:
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
