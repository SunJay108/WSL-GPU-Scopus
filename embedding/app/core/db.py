"""
SQLite database of the embedding service: administrator accounts, login
sessions and the activity log (one file in the state folder).
"""

import os
import sqlite3
import threading
from contextlib import contextmanager

from .config import ACCOUNTS_DB_PATH


# Serialises writes from the web server's threads.
write_lock = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    username       TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash  TEXT NOT NULL,
    role           TEXT NOT NULL DEFAULT 'user',      -- user | admin
    disabled       INTEGER NOT NULL DEFAULT 0,
    created_at     REAL NOT NULL,
    last_login_at  REAL
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash  TEXT PRIMARY KEY,                     -- sha256 of the cookie value
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  REAL NOT NULL,
    expires_at  REAL NOT NULL,
    ip          TEXT,
    user_agent  TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at       REAL NOT NULL,
    user_id          INTEGER,
    username         TEXT,                            -- kept even if the account changes
    action           TEXT NOT NULL,
    conversation_id  TEXT,
    detail           TEXT,                            -- JSON
    ip               TEXT
);

CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at DESC);
"""


@contextmanager
def connect():
    """Connection that commits on success and is always closed."""
    os.makedirs(os.path.dirname(ACCOUNTS_DB_PATH), exist_ok=True)
    conn = sqlite3.connect(ACCOUNTS_DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = NORMAL")
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init():
    with write_lock, connect() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(_SCHEMA)
