"""
Chat history (conversations and their messages) in a local SQLite file.

The file lives under app/data/history, so it survives container restarts and
is part of the WSL export / backups.
"""

import json
import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager

from config import HISTORY_DB_PATH


_lock = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id  TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role             TEXT NOT NULL,          -- user | assistant
    content          TEXT NOT NULL,          -- question, or the answer as Markdown
    data             TEXT,                   -- JSON: structured answer for the UI
    created_at       REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, id);
CREATE INDEX IF NOT EXISTS idx_conversations_updated ON conversations(updated_at DESC);
"""


@contextmanager
def _db():
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
    with _lock, _db() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(_SCHEMA)


def _title_from(question: str) -> str:
    title = " ".join(question.split())
    return title if len(title) <= 80 else title[:77].rstrip() + "…"


def create_conversation(title="New chat") -> dict:
    now = time.time()
    conv = {"id": uuid.uuid4().hex, "title": title, "created_at": now, "updated_at": now}
    with _lock, _db() as conn:
        conn.execute(
            "INSERT INTO conversations (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (conv["id"], conv["title"], now, now),
        )
    return conv


def list_conversations(query=None, limit=500):
    sql = "SELECT id, title, created_at, updated_at FROM conversations"
    args = []
    if query:
        sql += (
            " WHERE title LIKE ? OR id IN "
            "(SELECT conversation_id FROM messages WHERE content LIKE ?)"
        )
        args = [f"%{query}%", f"%{query}%"]
    sql += " ORDER BY updated_at DESC LIMIT ?"
    args.append(limit)
    with _db() as conn:
        return [dict(row) for row in conn.execute(sql, args)]


def get_conversation(conversation_id):
    with _db() as conn:
        row = conn.execute(
            "SELECT id, title, created_at, updated_at FROM conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()
        if row is None:
            return None
        messages = []
        for m in conn.execute(
            "SELECT id, role, content, data, created_at FROM messages "
            "WHERE conversation_id = ? ORDER BY id",
            (conversation_id,),
        ):
            msg = dict(m)
            msg["data"] = json.loads(msg["data"]) if msg["data"] else None
            messages.append(msg)
    return {**dict(row), "messages": messages}


def add_message(conversation_id, role, content, data=None) -> dict:
    """Store a message; the first question also becomes the conversation title."""
    now = time.time()
    with _lock, _db() as conn:
        cur = conn.execute(
            "INSERT INTO messages (conversation_id, role, content, data, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (conversation_id, role, content, json.dumps(data, ensure_ascii=False) if data else None, now),
        )
        if role == "user":
            conn.execute(
                "UPDATE conversations SET title = ? WHERE id = ? AND title = 'New chat'",
                (_title_from(content), conversation_id),
            )
        conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conversation_id))
        return {"id": cur.lastrowid, "role": role, "content": content, "data": data, "created_at": now}


def rename_conversation(conversation_id, title) -> bool:
    title = " ".join((title or "").split())[:200] or "Untitled"
    with _lock, _db() as conn:
        cur = conn.execute("UPDATE conversations SET title = ? WHERE id = ?", (title, conversation_id))
        return cur.rowcount > 0


def delete_conversation(conversation_id) -> bool:
    with _lock, _db() as conn:
        cur = conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
        return cur.rowcount > 0


def delete_all() -> int:
    with _lock, _db() as conn:
        count = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        conn.execute("DELETE FROM messages")
        conn.execute("DELETE FROM conversations")
    with _lock, _db() as conn:
        conn.execute("VACUUM")
    return count


def context_turns(conversation_id):
    """
    Previous question/answer pairs for understanding follow-up questions.
    Questions that were refused by the guard are left out.
    """
    conv = get_conversation(conversation_id)
    if conv is None:
        return []
    turns, pending = [], None
    for msg in conv["messages"]:
        if msg["role"] == "user":
            pending = msg["content"]
        elif pending is not None:
            data = msg["data"] or {}
            if data.get("status") in ("answered", "no_results"):
                turns.append({
                    "question": data.get("standalone_question") or pending,
                    "answer": data.get("summary") or "",
                })
            pending = None
    return turns


def export_markdown(conversation_id):
    conv = get_conversation(conversation_id)
    if conv is None:
        return None
    lines = [f"# {conv['title']}", ""]
    for msg in conv["messages"]:
        stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(msg["created_at"]))
        if msg["role"] == "user":
            lines += [f"## 🙋 {msg['content']}", f"*{stamp}*", ""]
        else:
            lines += [msg["content"], "", "---", ""]
    return "\n".join(lines)
