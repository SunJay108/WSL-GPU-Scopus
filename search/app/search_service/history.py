"""
Chat history (conversations and their messages).

Each conversation belongs to a user.  Deleting a chat only hides it from its
owner (deleted_at is set); administrators can still open it in the admin panel.
"""

import json
import time
import uuid

from core.db import connect, write_lock


_COLUMNS = "c.id, c.title, c.created_at, c.updated_at, c.user_id, c.deleted_at"


def _title_from(question: str) -> str:
    title = " ".join(question.split())
    return title if len(title) <= 80 else title[:77].rstrip() + "…"


def create_conversation(user_id, title="New chat") -> dict:
    now = time.time()
    conv = {"id": uuid.uuid4().hex, "title": title, "created_at": now, "updated_at": now,
            "user_id": user_id, "deleted_at": None}
    with write_lock, connect() as conn:
        conn.execute(
            "INSERT INTO conversations (id, title, created_at, updated_at, user_id) VALUES (?, ?, ?, ?, ?)",
            (conv["id"], title, now, now, user_id),
        )
    return conv


def list_conversations(user_id, query=None, limit=500):
    """The user's own chats that are not deleted, newest first."""
    sql = f"SELECT {_COLUMNS} FROM conversations c WHERE c.user_id = ? AND c.deleted_at IS NULL"
    args = [user_id]
    if query:
        sql += " AND (c.title LIKE ? OR c.id IN (SELECT conversation_id FROM messages WHERE content LIKE ?))"
        args += [f"%{query}%", f"%{query}%"]
    sql += " ORDER BY c.updated_at DESC LIMIT ?"
    args.append(limit)
    with connect() as conn:
        return [dict(row) for row in conn.execute(sql, args)]


def list_all_conversations(user_id=None, status="all", query=None, limit=500):
    """Admin view: every chat with its owner; status = all | active | deleted."""
    sql = (
        f"SELECT {_COLUMNS}, u.username, "
        "(SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id AND m.role = 'user') AS questions "
        "FROM conversations c LEFT JOIN users u ON u.id = c.user_id WHERE 1=1"
    )
    args = []
    if user_id:
        sql += " AND c.user_id = ?"
        args.append(user_id)
    if status == "active":
        sql += " AND c.deleted_at IS NULL"
    elif status == "deleted":
        sql += " AND c.deleted_at IS NOT NULL"
    if query:
        sql += " AND (c.title LIKE ? OR c.id IN (SELECT conversation_id FROM messages WHERE content LIKE ?))"
        args += [f"%{query}%", f"%{query}%"]
    sql += " ORDER BY c.updated_at DESC LIMIT ?"
    args.append(limit)
    with connect() as conn:
        return [dict(row) for row in conn.execute(sql, args)]


def get_conversation(conversation_id, user_id=None):
    """
    A conversation with its messages.  With user_id: only the user's own,
    not deleted chat (otherwise None).  Without: any chat (admin).
    """
    with connect() as conn:
        row = conn.execute(
            f"SELECT {_COLUMNS}, u.username FROM conversations c "
            "LEFT JOIN users u ON u.id = c.user_id WHERE c.id = ?",
            (conversation_id,),
        ).fetchone()
        if row is None or (user_id is not None and (row["user_id"] != user_id or row["deleted_at"])):
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
    with write_lock, connect() as conn:
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


def rename_conversation(conversation_id, user_id, title):
    """Rename the user's chat; returns (old_title, new_title) or None."""
    title = " ".join((title or "").split())[:200] or "Untitled"
    with write_lock, connect() as conn:
        row = conn.execute(
            "SELECT title FROM conversations WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
            (conversation_id, user_id),
        ).fetchone()
        if row is None:
            return None
        conn.execute("UPDATE conversations SET title = ? WHERE id = ?", (title, conversation_id))
    return row["title"], title


def delete_conversation(conversation_id, user_id):
    """Hide the user's chat from them; returns {title, questions} or None."""
    with write_lock, connect() as conn:
        row = conn.execute(
            "SELECT title, (SELECT COUNT(*) FROM messages WHERE conversation_id = ? AND role = 'user') AS questions "
            "FROM conversations WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
            (conversation_id, conversation_id, user_id),
        ).fetchone()
        if row is None:
            return None
        conn.execute("UPDATE conversations SET deleted_at = ? WHERE id = ?", (time.time(), conversation_id))
    return dict(row)


def delete_all(user_id) -> int:
    with write_lock, connect() as conn:
        cur = conn.execute(
            "UPDATE conversations SET deleted_at = ? WHERE user_id = ? AND deleted_at IS NULL",
            (time.time(), user_id),
        )
        return cur.rowcount


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


def export_markdown(conv) -> str:
    lines = [f"# {conv['title']}", ""]
    for msg in conv["messages"]:
        stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(msg["created_at"]))
        if msg["role"] == "user":
            lines += [f"## 🙋 {msg['content']}", f"*{stamp}*", ""]
        else:
            lines += [msg["content"], "", "---", ""]
    return "\n".join(lines)
