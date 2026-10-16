"""
Activity log: who did what and when (shown in the admin panel).

Entries are only ever added, never changed or deleted through the UI.
"""

import json
import time

from .db import connect, write_lock


ACTIONS = {
    "signup": "Created account",
    "login": "Logged in",
    "login_failed": "Failed login",
    "logout": "Logged out",
    "password_change": "Changed password",
    "ask": "Asked a question",
    "rename_chat": "Renamed chat",
    "delete_chat": "Deleted chat",
    "clear_history": "Deleted all chats",
    "export_chat": "Exported chat",
    "index_import_start": "Index: import started",
    "index_import_completed": "Index: import finished",
    "index_import_failed": "Index: import failed",
    "bundle_deleted": "Index: deleted bundle",
    # Entries of the earlier built-in embedding service (kept for old log entries).
    "upload_folder": "Embedding: uploaded folder",
    "ingest_start": "Embedding: started ingest",
    "ingest_completed": "Embedding: ingest finished",
    "ingest_failed": "Embedding: ingest failed",
    "ingest_cancel": "Embedding: cancel requested",
    "ingest_cancelled": "Embedding: ingest cancelled",
    "admin_create_user": "Admin: created user",
    "admin_disable_user": "Admin: disabled user",
    "admin_enable_user": "Admin: enabled user",
    "admin_set_role": "Admin: changed role",
    "admin_reset_password": "Admin: reset password",
}


def log(action, user=None, conversation_id=None, detail=None, ip=None, username=None):
    with write_lock, connect() as conn:
        conn.execute(
            "INSERT INTO audit_log (created_at, user_id, username, action, conversation_id, detail, ip) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                time.time(),
                user["id"] if user else None,
                user["username"] if user else username,
                action,
                conversation_id,
                json.dumps(detail, ensure_ascii=False) if detail else None,
                ip,
            ),
        )


def query(user_id=None, action=None, text=None, before_id=None, limit=100):
    """Newest entries first; page with before_id (the last id of the previous page)."""
    sql = (
        "SELECT a.*, c.title AS conversation_title, c.deleted_at AS conversation_deleted_at "
        "FROM audit_log a LEFT JOIN conversations c ON c.id = a.conversation_id WHERE 1=1"
    )
    args = []
    if user_id:
        sql += " AND a.user_id = ?"
        args.append(user_id)
    if action:
        sql += " AND a.action = ?"
        args.append(action)
    if text:
        sql += " AND (a.detail LIKE ? OR a.username LIKE ? OR c.title LIKE ?)"
        args += [f"%{text}%"] * 3
    if before_id:
        sql += " AND a.id < ?"
        args.append(before_id)
    sql += " ORDER BY a.id DESC LIMIT ?"
    args.append(min(int(limit), 500))

    with connect() as conn:
        rows = [dict(r) for r in conn.execute(sql, args)]
    for row in rows:
        row["detail"] = json.loads(row["detail"]) if row["detail"] else None
        row["action_label"] = ACTIONS.get(row["action"], row["action"])
    return rows


def stats():
    today = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))
    with connect() as conn:
        one = lambda sql, *a: conn.execute(sql, a).fetchone()[0]
        return {
            "users": one("SELECT COUNT(*) FROM users"),
            "active_today": one("SELECT COUNT(DISTINCT user_id) FROM audit_log WHERE created_at >= ?", today),
            "chats": one("SELECT COUNT(*) FROM conversations"),
            "deleted_chats": one("SELECT COUNT(*) FROM conversations WHERE deleted_at IS NOT NULL"),
            "questions": one("SELECT COUNT(*) FROM audit_log WHERE action = 'ask'"),
            "questions_today": one("SELECT COUNT(*) FROM audit_log WHERE action = 'ask' AND created_at >= ?", today),
            "blocked": one(
                "SELECT COUNT(*) FROM audit_log WHERE action = 'ask' AND json_extract(detail, '$.status') = 'blocked'"
            ),
            "failed_logins_today": one(
                "SELECT COUNT(*) FROM audit_log WHERE action = 'login_failed' AND created_at >= ?", today
            ),
        }
