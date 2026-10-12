"""
Scopus AI search UI (port 7860): chat with history, answers from the local paper index.

    GET    /                                UI
    GET    /api/status                      index + model state
    GET    /api/conversations?q=            history list (optionally searched)
    POST   /api/conversations               new conversation
    GET    /api/conversations/{id}          messages (+ state of an answer in progress)
    PATCH  /api/conversations/{id}          rename
    DELETE /api/conversations/{id}          delete
    DELETE /api/conversations               clear all history
    GET    /api/conversations/{id}/export   Markdown download
    POST   /api/conversations/{id}/ask      ask; progress is streamed as Server-Sent Events
"""

import json
import os
import queue
import re
import threading
import traceback
from urllib.parse import quote

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import history
from config import EMBED_UI_PORT
from query import answer_question, index_info, models_loaded


WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")

app = FastAPI(title="Scopus AI", docs_url="/api/docs", redoc_url=None)
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
history.init()

# Answers in progress: conversation id -> {"stage", "text", "partial"}.
# The answer keeps running (and is saved) even if the browser disconnects.
_pending = {}
_pending_lock = threading.Lock()


class AskBody(BaseModel):
    question: str


class RenameBody(BaseModel):
    title: str


def _sse(event, data):
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _require(conversation_id):
    conv = history.get_conversation(conversation_id)
    if conv is None:
        raise HTTPException(404, "Conversation not found.")
    return conv


@app.get("/", include_in_schema=False)
def index_page():
    return FileResponse(os.path.join(WEB_DIR, "index.html"), headers={"Cache-Control": "no-cache"})


@app.get("/api/status")
def status():
    with _pending_lock:
        busy = bool(_pending)
    return {
        "index": index_info(),
        "models_loaded": models_loaded(),
        "busy": busy,
        "embed_ui_port": EMBED_UI_PORT,
    }


@app.get("/api/conversations")
def list_conversations(q: str = ""):
    return history.list_conversations(q.strip() or None)


@app.post("/api/conversations")
def create_conversation():
    return history.create_conversation()


@app.get("/api/conversations/{conversation_id}")
def get_conversation(conversation_id: str):
    conv = _require(conversation_id)
    with _pending_lock:
        pending = _pending.get(conversation_id)
        conv["pending"] = dict(pending) if pending else None
    return conv


@app.patch("/api/conversations/{conversation_id}")
def rename_conversation(conversation_id: str, body: RenameBody):
    if not history.rename_conversation(conversation_id, body.title):
        raise HTTPException(404, "Conversation not found.")
    return {"ok": True}


@app.delete("/api/conversations/{conversation_id}")
def delete_conversation(conversation_id: str):
    with _pending_lock:
        if conversation_id in _pending:
            raise HTTPException(409, "This conversation is still being answered.")
    if not history.delete_conversation(conversation_id):
        raise HTTPException(404, "Conversation not found.")
    return {"ok": True}


@app.delete("/api/conversations")
def delete_all_conversations():
    with _pending_lock:
        if _pending:
            raise HTTPException(409, "A question is still being answered; try again when it has finished.")
    return {"deleted": history.delete_all()}


@app.get("/api/conversations/{conversation_id}/export")
def export_conversation(conversation_id: str):
    markdown = history.export_markdown(conversation_id)
    if markdown is None:
        raise HTTPException(404, "Conversation not found.")
    title = _require(conversation_id)["title"]
    filename = (re.sub(r"[^\w\- ]+", "", title).strip() or "conversation")[:60] + ".md"
    return Response(
        markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@app.post("/api/conversations/{conversation_id}/ask")
def ask(conversation_id: str, body: AskBody):
    question = body.question.strip()
    if not question:
        raise HTTPException(400, "Please enter a question.")
    if len(question) > 2000:
        raise HTTPException(400, "The question is too long (maximum 2000 characters).")
    _require(conversation_id)

    with _pending_lock:
        if conversation_id in _pending:
            raise HTTPException(409, "This conversation is still being answered.")
        _pending[conversation_id] = {"stage": "queued", "text": "Starting…", "partial": ""}

    turns = history.context_turns(conversation_id)
    user_message = history.add_message(conversation_id, "user", question)
    events = queue.Queue()

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
            events.put(("done", {"message": message}))
        except Exception as exc:
            traceback.print_exc()
            text = f"⚠️ Something went wrong while answering: {exc}"
            message = history.add_message(
                conversation_id, "assistant", text, {"status": "error", "message": text}
            )
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


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=7860, access_log=False)
