"""
Activity log of the embedding service: who signed in, which folders were
uploaded and ingested, and which bundles were exported.  Entries are only
ever added.
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
    "upload_folder": "Uploaded folder",
    "ingest_start": "Started ingest",
    "ingest_completed": "Ingest finished",
    "ingest_failed": "Ingest failed",
    "ingest_cancel": "Cancel requested",
    "ingest_cancelled": "Ingest cancelled",
    "export_start": "Started export",
    "export_completed": "Export finished",
    "export_failed": "Export failed",
    "export_deleted": "Deleted export",
    "admin_create_user": "Created user",
    "admin_reset_password": "Reset password",
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


def recent(limit=50):
    """Newest entries first."""
    with connect() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (int(limit),))]
    for row in rows:
        row["detail"] = json.loads(row["detail"]) if row["detail"] else None
        row["action_label"] = ACTIONS.get(row["action"], row["action"])
    return rows
