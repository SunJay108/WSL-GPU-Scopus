"""
Scopus AI search UI (port 7860): chat with history, user accounts and an
admin panel; answers come from the local paper index.

Pages
    GET  /                          chat UI (shows the sign-in screen when needed)
    GET  /admin                     admin panel (administrators only)

Accounts (web_auth.py)
    GET  /api/auth/state            signed-in user, whether sign-up is open
    POST /api/auth/signup           create account (the first one becomes admin)
    POST /api/auth/login            sign in (sets an HttpOnly session cookie)
    POST /api/auth/logout           sign out
    POST /api/auth/password         change own password

Chats (own chats only)
    GET    /api/status              index + model state
    GET    /api/conversations?q=    history list (optionally searched)
    POST   /api/conversations       new conversation
    GET    /api/conversations/{id}  messages (+ state of an answer in progress)
    PATCH  /api/conversations/{id}  rename
    DELETE /api/conversations/{id}  delete (hidden from the user, kept for admins)
    DELETE /api/conversations       delete all own chats
    GET    /api/conversations/{id}/export   Markdown download
    POST   /api/conversations/{id}/ask      ask; progress is streamed as Server-Sent Events

Admin
    GET   /api/admin/stats | activity | users | conversations | conversations/{id}
    POST  /api/admin/users          add a user
    PATCH /api/admin/users/{id}     disable/enable, change role, reset password
"""

import json
import os
import queue
import re
import threading
import traceback
from typing import Optional
from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from core import audit, auth, db
from . import history
from .query import answer_question, index_info, models_loaded
from core.web_auth import WebAuth, client_ip as _ip


STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

app = FastAPI(title="Scopus AI", docs_url="/api/docs", redoc_url=None)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
db.init()

# Sign-up / login (/api/auth/*); this service's own session cookie.
web_auth = WebAuth(cookie="scopus_session", service="search")
web_auth.install(app)
current_user = web_auth.current_user
admin_user = web_auth.admin_user

# Answers in progress: conversation id -> {"user_id", "stage", "text", "partial"}.
# The answer keeps running (and is saved) even if the browser disconnects.
_pending = {}
_pending_lock = threading.Lock()


# =====================================================
# Request models / helpers
# =====================================================

class AskBody(BaseModel):
    question: str


class RenameBody(BaseModel):
    title: str


class NewUser(BaseModel):
    username: str
    password: str
    role: str = "user"


class UserUpdate(BaseModel):
    disabled: Optional[bool] = None
    role: Optional[str] = None
    password: Optional[str] = None


def _sse(event, data):
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _own_conversation(conversation_id, user):
    conv = history.get_conversation(conversation_id, user_id=user["id"])
    if conv is None:
        raise HTTPException(404, "Conversation not found.")
    return conv


def _busy(user_id=None, conversation_id=None) -> bool:
    with _pending_lock:
        if conversation_id is not None:
            return conversation_id in _pending
        return any(p["user_id"] == user_id for p in _pending.values())


# =====================================================
# Pages
# =====================================================

@app.get("/", include_in_schema=False)
def index_page():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"), headers={"Cache-Control": "no-cache"})


@app.get("/admin", include_in_schema=False)
def admin_page():
    return FileResponse(os.path.join(STATIC_DIR, "admin.html"), headers={"Cache-Control": "no-cache"})


# =====================================================
# Chats
# =====================================================

@app.get("/api/status")
def status(user: dict = Depends(current_user)):
    return {
        "index": index_info(),
        "models_loaded": models_loaded(),
        "busy": _busy(user_id=user["id"]),
        "user": auth.public(user),
    }


@app.get("/api/conversations")
def list_conversations(q: str = "", user: dict = Depends(current_user)):
    return history.list_conversations(user["id"], q.strip() or None)


@app.post("/api/conversations")
def create_conversation(user: dict = Depends(current_user)):
    return history.create_conversation(user["id"])


@app.get("/api/conversations/{conversation_id}")
def get_conversation(conversation_id: str, user: dict = Depends(current_user)):
    conv = _own_conversation(conversation_id, user)
    with _pending_lock:
        pending = _pending.get(conversation_id)
        conv["pending"] = {k: v for k, v in pending.items() if k != "user_id"} if pending else None
    return conv


@app.patch("/api/conversations/{conversation_id}")
def rename_conversation(conversation_id: str, body: RenameBody, request: Request,
                        user: dict = Depends(current_user)):
    renamed = history.rename_conversation(conversation_id, user["id"], body.title)
    if renamed is None:
        raise HTTPException(404, "Conversation not found.")
    audit.log("rename_chat", user, conversation_id, {"old": renamed[0], "new": renamed[1]}, ip=_ip(request))
    return {"ok": True}


@app.delete("/api/conversations/{conversation_id}")
def delete_conversation(conversation_id: str, request: Request, user: dict = Depends(current_user)):
    if _busy(conversation_id=conversation_id):
        raise HTTPException(409, "This conversation is still being answered.")
    deleted = history.delete_conversation(conversation_id, user["id"])
    if deleted is None:
        raise HTTPException(404, "Conversation not found.")
    audit.log("delete_chat", user, conversation_id, deleted, ip=_ip(request))
    return {"ok": True}


@app.delete("/api/conversations")
def delete_all_conversations(request: Request, user: dict = Depends(current_user)):
    if _busy(user_id=user["id"]):
        raise HTTPException(409, "A question is still being answered; try again when it has finished.")
    count = history.delete_all(user["id"])
    audit.log("clear_history", user, detail={"chats": count}, ip=_ip(request))
    return {"deleted": count}


@app.get("/api/conversations/{conversation_id}/export")
def export_conversation(conversation_id: str, request: Request, user: dict = Depends(current_user)):
    conv = _own_conversation(conversation_id, user)
    audit.log("export_chat", user, conversation_id, {"title": conv["title"]}, ip=_ip(request))
    filename = (re.sub(r"[^\w\- ]+", "", conv["title"]).strip() or "conversation")[:60] + ".md"
    return Response(
        history.export_markdown(conv),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@app.post("/api/conversations/{conversation_id}/ask")
def ask(conversation_id: str, body: AskBody, request: Request, user: dict = Depends(current_user)):
    question = body.question.strip()
    if not question:
        raise HTTPException(400, "Please enter a question.")
    if len(question) > 2000:
        raise HTTPException(400, "The question is too long (maximum 2000 characters).")
    _own_conversation(conversation_id, user)

    with _pending_lock:
        if conversation_id in _pending:
            raise HTTPException(409, "This conversation is still being answered.")
        _pending[conversation_id] = {"user_id": user["id"], "stage": "queued", "text": "Starting…", "partial": ""}

    turns = history.context_turns(conversation_id)
    user_message = history.add_message(conversation_id, "user", question)
    events = queue.Queue()
    ip = _ip(request)

    def progress(event, **data):
        with _pending_lock:
            state = _pending.get(conversation_id)
            if state is not None:
                if event == "stage":
                    state.update(stage=data["stage"], text=data["text"])
                elif event == "delta":
                    state["partial"] = data["text"]
        events.put((event, data))

    def work():
        try:
            result = answer_question(question, history=turns, progress=progress)
            data = {k: v for k, v in result.items() if k != "markdown"}
            message = history.add_message(conversation_id, "assistant", result["markdown"], data)
            guard = result.get("guard") or {}
            audit.log("ask", user, conversation_id, {
                "question": question,
                "status": result["status"],
                "category": guard.get("category"),
                "reason": guard.get("reason"),
                "searched_as": result.get("standalone_question"),
                "top_score": result.get("top_score"),
                "seconds": result.get("seconds"),
            }, ip=ip)
            events.put(("done", {"message": message}))
        except Exception as exc:
            traceback.print_exc()
            text = f"⚠️ Something went wrong while answering: {exc}"
            message = history.add_message(
                conversation_id, "assistant", text, {"status": "error", "message": text}
            )
            audit.log("ask", user, conversation_id,
                      {"question": question, "status": "error", "error": str(exc)}, ip=ip)
            events.put(("error", {"message": message}))
        finally:
            with _pending_lock:
                _pending.pop(conversation_id, None)

    threading.Thread(target=work, name=f"answer-{conversation_id[:8]}", daemon=True).start()

    def stream():
        yield _sse("user", {"message": user_message})
        while True:
            try:
                event, data = events.get(timeout=15)
            except queue.Empty:
                yield ": keep-alive\n\n"
                continue
            yield _sse(event, data)
            if event in ("done", "error"):
                break

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# =====================================================
# Admin
# =====================================================

@app.get("/api/admin/stats")
def admin_stats(admin: dict = Depends(admin_user)):
    return audit.stats()


@app.get("/api/admin/activity")
def admin_activity(user_id: int = 0, action: str = "", q: str = "", before: int = 0, limit: int = 100,
                   admin: dict = Depends(admin_user)):
    return {
        "items": audit.query(user_id or None, action or None, q.strip() or None, before or None, limit),
        "actions": audit.ACTIONS,
    }


@app.get("/api/admin/users")
def admin_users(admin: dict = Depends(admin_user)):
    return auth.list_users()


@app.post("/api/admin/users")
def admin_create_user(body: NewUser, request: Request, admin: dict = Depends(admin_user)):
    user = auth.create_user(body.username, body.password, role=body.role)
    audit.log("admin_create_user", admin, detail={"target": user["username"], "role": user["role"]},
              ip=_ip(request))
    return auth.public(user)


@app.patch("/api/admin/users/{user_id}")
def admin_update_user(user_id: int, body: UserUpdate, request: Request, admin: dict = Depends(admin_user)):
    target = auth.get_user(user_id)
    if target is None:
        raise HTTPException(404, "User not found.")
    ip = _ip(request)

    if body.disabled is not None and bool(body.disabled) != bool(target["disabled"]):
        if user_id == admin["id"] and body.disabled:
            raise HTTPException(409, "You cannot disable your own account.")
        auth.set_disabled(user_id, body.disabled)
        audit.log("admin_disable_user" if body.disabled else "admin_enable_user", admin,
                  detail={"target": target["username"]}, ip=ip)

    if body.role and body.role != target["role"]:
        if user_id == admin["id"]:
            raise HTTPException(409, "You cannot change your own role.")
        auth.set_role(user_id, body.role)
        audit.log("admin_set_role", admin,
                  detail={"target": target["username"], "role": body.role, "previous": target["role"]}, ip=ip)

    if body.password:
        auth.set_password(user_id, body.password)
        auth.delete_user_sessions(user_id)
        audit.log("admin_reset_password", admin, detail={"target": target["username"]}, ip=ip)

    return auth.public(auth.get_user(user_id))


@app.get("/api/admin/conversations")
def admin_conversations(user_id: int = 0, status: str = "all", q: str = "", admin: dict = Depends(admin_user)):
    return history.list_all_conversations(user_id or None, status, q.strip() or None)


@app.get("/api/admin/conversations/{conversation_id}")
def admin_conversation(conversation_id: str, admin: dict = Depends(admin_user)):
    conv = history.get_conversation(conversation_id)
    if conv is None:
        raise HTTPException(404, "Conversation not found.")
    return conv

