"""
SQLite database of the search UI: user accounts, login sessions, chat
history and the activity log (all in one file under app/data/history).
"""

import os
import sqlite3
import threading
from contextlib import contextmanager

from config import HISTORY_DB_PATH


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

CREATE TABLE IF NOT EXISTS conversations (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    user_id     INTEGER REFERENCES users(id),
    deleted_at  REAL                                  -- deleted by the user, kept for admins
);

CREATE TABLE IF NOT EXISTS messages (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id  TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role             TEXT NOT NULL,                   -- user | assistant
    content          TEXT NOT NULL,                   -- question, or the answer as Markdown
    data             TEXT,                            -- JSON: structured answer for the UI
    created_at       REAL NOT NULL
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
"""

_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, id);
CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations(user_id, deleted_at, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_conversations_updated ON conversations(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action, created_at DESC);
"""


@contextmanager
def connect():
    """Connection that commits on success and is always closed."""
    os.makedirs(os.path.dirname(HISTORY_DB_PATH), exist_ok=True)
    conn = sqlite3.connect(HISTORY_DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init():
    with write_lock, connect() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(_SCHEMA)
        # Chat tables created before accounts existed lack these columns.
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(conversations)")}
        if "user_id" not in columns:
            conn.execute("ALTER TABLE conversations ADD COLUMN user_id INTEGER REFERENCES users(id)")
        if "deleted_at" not in columns:
            conn.execute("ALTER TABLE conversations ADD COLUMN deleted_at REAL")
        conn.executescript(_INDEXES)
