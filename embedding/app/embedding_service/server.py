"""
Embedding service: web UI + REST API that builds the search index and
exports it for the search app.  It has its own accounts (administrators
only) and runs on its own PC; the search app never connects to it.

    GET    /                       UI (shows the sign-in screen when needed)
    GET    /api/auth/...           sign in / sign out (web_auth.py)
    GET    /api/folders?path=      browse IMPORT_ROOT and preview importable files
    POST   /api/uploads            create an upload folder (browser folder upload)
    PUT    /api/uploads/{id}/{fn}  stream one file into it
    POST   /api/ingest             start an ingest job
    POST   /api/cancel             cancel the running job
    GET    /api/status             job and export progress, index, service health
    GET    /api/exports            export bundles in EXPORT_DIR
    POST   /api/exports            export the current index (Qdrant snapshot bundle)
    DELETE /api/exports/{name}     delete a bundle

An ingest and an export never run at the same time: a snapshot must not
change while it is written.
"""

import os
import re
import subprocess
import threading
import time

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from core import audit, db, paper_index
from core.config import (
    EMBED_DEVICE,
    EXPORT_HOST_DIR,
    IMPORT_HOST_DIR,
    IMPORT_ROOT,
    QDRANT_COLLECTION,
    QDRANT_URL,
)
from core.index_meta import read_index_meta
from core.web_auth import WebAuth, client_ip

from . import exporter
from .errors import IngestError
from .jobs import JobBusyError, JobManager
from .pipeline import resolve_live_collection
from .sources import MYISAM_EXTENSIONS, RIS_EXTENSIONS, UPLOAD_EXTENSIONS, mariadb_connect, scan_folder


STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
UPLOADS_DIR = "uploads"
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")

app = FastAPI(title="Scopus AI – Embedding service", docs_url="/api/docs", redoc_url=None)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

db.init()
web_auth = WebAuth(cookie="embed_session", service="embedding")
web_auth.install(app)
admin_user = web_auth.admin_user

jobs = JobManager()
exports = exporter.ExportManager()
exporter.cleanup_partial()
# Checked and started together, so an ingest and an export cannot both start.
_start_lock = threading.Lock()


# =====================================================
# Import folder helpers
# =====================================================

def _root():
    return os.path.realpath(IMPORT_ROOT)


def resolve_import_path(rel: str) -> str:
    root = _root()
    target = os.path.realpath(os.path.join(root, (rel or "").strip().lstrip("/")))
    if target != root and not target.startswith(root + os.sep):
        raise HTTPException(400, "Path is outside the import folder.")
    if not os.path.isdir(target):
        raise HTTPException(404, f"Folder not found: {rel}")
    return target


def to_rel(path: str) -> str:
    rel = os.path.relpath(path, _root())
    return "" if rel == "." else rel.replace(os.sep, "/")


def display_path(rel: str) -> str:
    base = IMPORT_HOST_DIR.rstrip("/")
    return f"{base}/{rel}" if rel else base


def _quick_counts(path: str):
    """Importable files in a sub-folder, by name only (no stat calls)."""
    stems = {}
    ris = 0
    try:
        names = os.listdir(path)
    except OSError:
        return 0, 0
    for name in names:
        stem, ext = os.path.splitext(name)
        ext = ext.lower()
        if ext in MYISAM_EXTENSIONS:
            stems.setdefault(stem, set()).add(ext)
        elif ext in RIS_EXTENSIONS:
            ris += 1
    tables = sum(1 for exts in stems.values() if {".frm", ".myd"} <= exts)
    return tables, ris


# =====================================================
# Health / index info (cached; the UI polls every second)
# =====================================================

_cache = {}
_cache_lock = threading.Lock()


def _cached(key, ttl, fn):
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    value = fn()
    with _cache_lock:
        _cache[key] = (now, value)
    return value


def _invalidate(*keys):
    with _cache_lock:
        for key in keys:
            _cache.pop(key, None)


def _detect_device():
    """
    Query the GPU with nvidia-smi instead of torch: initialising CUDA here would
    pin a CUDA context (~300 MB VRAM) in this long-running process.
    """
    if not EMBED_DEVICE.startswith("cuda"):
        return {"ok": True, "detail": f"CPU (EMBED_DEVICE={EMBED_DEVICE})"}
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.used,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip().splitlines()[0]
        name, used, total = [part.strip() for part in out.split(",")]
        free_gb = (int(total) - int(used)) / 1024
        return {
            "ok": True,
            "detail": f"GPU · {name} · {free_gb:.1f} GB free",
            "free_mb": int(total) - int(used),
        }
    except Exception:
        return {"ok": False, "detail": "CPU (no GPU visible – embedding will be slow)"}


def _qdrant():
    from qdrant_client import QdrantClient
    return QdrantClient(url=QDRANT_URL, timeout=5)


def _services():
    services = {"device": _detect_device()}

    try:
        client = _qdrant()
        client.get_collections()
        services["qdrant"] = {"ok": True, "detail": "connected"}
    except Exception as exc:
        services["qdrant"] = {"ok": False, "detail": f"unreachable ({type(exc).__name__})"}

    try:
        with mariadb_connect(connect_timeout=3) as conn, conn.cursor() as cur:
            cur.execute("SELECT VERSION()")
            services["mariadb"] = {"ok": True, "detail": cur.fetchone()[0].split("-")[0]}
    except IngestError:
        services["mariadb"] = {"ok": False, "detail": "unreachable"}
    except Exception as exc:
        services["mariadb"] = {"ok": False, "detail": f"error ({type(exc).__name__})"}

    return services


def _index_info():
    meta = read_index_meta()
    info = {"meta": meta, "collection": None, "points": None, "error": None,
            "exportable": bool(meta) and meta.get("format") == paper_index.INDEX_FORMAT}
    try:
        client = _qdrant()
        live = resolve_live_collection(client)
        info["collection"] = live
        if live:
            info["points"] = client.get_collection(live).points_count
        else:
            info["exportable"] = False
    except Exception as exc:
        info["error"] = f"Qdrant unreachable ({type(exc).__name__})"
        info["exportable"] = False
    return info


def _export_state():
    state = exports.snapshot()
    state["bundles"] = _cached("bundles", 10, exporter.list_bundles)
    return state


# =====================================================
# Routes
# =====================================================

@app.get("/", include_in_schema=False)
def index_page():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"), headers={"Cache-Control": "no-cache"})


@app.get("/api/folders")
def list_folder(path: str = "", admin: dict = Depends(admin_user)):
    target = resolve_import_path(path)
    rel = to_rel(target)

    folders = []
    try:
        entries = sorted(os.scandir(target), key=lambda e: e.name.lower())
    except OSError as exc:
        raise HTTPException(500, f"Cannot read folder: {exc}")

    for entry in entries:
        if entry.name.startswith(".") or not entry.is_dir():
            continue
        tables, ris = _quick_counts(entry.path)
        folders.append({"name": entry.name, "path": to_rel(entry.path), "tables": tables, "ris_files": ris})

    crumbs = [{"name": IMPORT_HOST_DIR, "path": ""}]
    acc = []
    for part in rel.split("/") if rel else []:
        acc.append(part)
        crumbs.append({"name": part, "path": "/".join(acc)})

    return {
        "path": rel,
        "display": display_path(rel),
        "parent": to_rel(os.path.dirname(target)) if rel else None,
        "breadcrumbs": crumbs,
        "folders": folders,
        "scan": scan_folder(target).to_dict(),
        "writable": os.access(_root(), os.W_OK),
    }


class IngestRequest(BaseModel):
    path: str
    tables: list[str] | None = None
    include_ris: bool = True
    # "append" = Add to index (default), "replace" = Initialize index
    mode: str = "append"


@app.post("/api/ingest", status_code=202)
def start_ingest(body: IngestRequest, request: Request, admin: dict = Depends(admin_user)):
    if body.mode not in ("replace", "append"):
        raise HTTPException(400, "mode must be 'append' (Add to index) or 'replace' (Initialize index).")

    folder = resolve_import_path(body.path)
    rel = to_rel(folder)
    with _start_lock:
        if exports.running():
            raise HTTPException(409, "An export is running. Start the ingest when it has finished.")
        try:
            job = jobs.start({
                "folder": folder,
                "path": rel,
                "label": display_path(rel),
                "tables": body.tables,
                "include_ris": body.include_ris,
                "mode": body.mode,
                "started_by": {"id": admin["id"], "username": admin["username"]},
            })
        except JobBusyError as exc:
            raise HTTPException(409, str(exc))
    audit.log("ingest_start", admin, detail={
        "service": "embedding", "job": job.id, "folder": display_path(rel), "mode": body.mode,
        "tables": body.tables, "include_ris": body.include_ris,
    }, ip=client_ip(request))
    return {"job_id": job.id}


@app.post("/api/cancel")
def cancel_ingest(request: Request, admin: dict = Depends(admin_user)):
    snapshot = jobs.snapshot() or {}
    if not jobs.cancel():
        raise HTTPException(409, "No ingest job is running.")
    audit.log("ingest_cancel", admin, detail={"service": "embedding", "job": snapshot.get("id")},
              ip=client_ip(request))
    return {"ok": True}


_last_state = {"job": None, "export": None}


@app.get("/api/status")
def status(log_since: int = 0, admin: dict = Depends(admin_user)):
    job = jobs.snapshot(log_since)
    export_state = exports.snapshot().get("state")

    # Refresh the index and bundle lists right after a job or an export ends.
    job_state = (job or {}).get("state")
    if job_state != _last_state["job"]:
        _last_state["job"] = job_state
        _invalidate("index")
    if export_state != _last_state["export"]:
        _last_state["export"] = export_state
        _invalidate("bundles")

    return {
        "job": job,
        "export": _export_state(),
        "index": _cached("index", 5, _index_info),
        "services": _cached("services", 10, _services),
        "config": {
            "import_root": IMPORT_HOST_DIR,
            "export_dir": EXPORT_HOST_DIR,
            "collection_alias": QDRANT_COLLECTION,
            "index_format": paper_index.INDEX_FORMAT,
        },
    }


@app.get("/api/exports")
def list_exports(admin: dict = Depends(admin_user)):
    _invalidate("bundles")
    return _export_state()


@app.post("/api/exports", status_code=202)
def start_export(request: Request, admin: dict = Depends(admin_user)):
    info = _index_info()
    if not info["exportable"]:
        raise HTTPException(409, info["error"] or "There is no index to export yet. Run an ingest first.")
    with _start_lock:
        if jobs.running():
            raise HTTPException(409, "An ingest job is running. Export when it has finished.")
        try:
            exports.start(admin)
        except exporter.ExportBusyError as exc:
            raise HTTPException(409, str(exc))
    audit.log("export_start", admin, detail={"service": "embedding", "index_version": info["meta"]["version"]},
              ip=client_ip(request))
    return {"ok": True}


@app.delete("/api/exports/{name}")
def delete_export(name: str, request: Request, admin: dict = Depends(admin_user)):
    if not re.fullmatch(r"[A-Za-z0-9._-]+", name) or name.startswith("."):
        raise HTTPException(400, "Invalid bundle name.")
    try:
        exporter.delete_bundle(name)
    except FileNotFoundError:
        raise HTTPException(404, "Bundle not found.")
    _invalidate("bundles")
    audit.log("export_deleted", admin, detail={"service": "embedding", "bundle": name}, ip=client_ip(request))
    return {"ok": True}


class UploadRequest(BaseModel):
    name: str = "folder"


@app.post("/api/uploads")
def create_upload(body: UploadRequest, request: Request, admin: dict = Depends(admin_user)):
    safe = _SAFE_NAME.sub("_", body.name).strip("._")[:60] or "folder"
    upload_id = f"{safe}-{time.strftime('%Y%m%d-%H%M%S')}"
    target = os.path.join(_root(), UPLOADS_DIR, upload_id)
    try:
        os.makedirs(target, exist_ok=False)
    except FileExistsError:
        raise HTTPException(409, "Upload folder already exists; try again in a second.")
    except OSError as exc:
        raise HTTPException(500, f"Cannot create upload folder (is the import folder writable?): {exc}")
    audit.log("upload_folder", admin, detail={"service": "embedding", "folder": display_path(to_rel(target))},
              ip=client_ip(request))
    return {"id": upload_id, "path": to_rel(target)}


@app.put("/api/uploads/{upload_id}/{filename}")
async def upload_file(upload_id: str, filename: str, request: Request, admin: dict = Depends(admin_user)):
    if not re.fullmatch(r"[A-Za-z0-9._-]+", upload_id) or upload_id.startswith("."):
        raise HTTPException(400, "Invalid upload id.")
    if (os.path.basename(filename) != filename or filename.startswith(".")
            or "\x00" in filename or "\\" in filename):
        raise HTTPException(400, "Invalid file name.")
    if os.path.splitext(filename)[1].lower() not in UPLOAD_EXTENSIONS:
        raise HTTPException(400, "Only .frm, .MYD, .MYI and .ris files can be uploaded.")

    folder = os.path.join(_root(), UPLOADS_DIR, upload_id)
    if not os.path.isdir(folder):
        raise HTTPException(404, "Upload folder not found.")

    dst = os.path.join(folder, filename)
    tmp = dst + ".part"
    size = 0
    try:
        with open(tmp, "wb") as f:
            async for chunk in request.stream():
                f.write(chunk)
                size += len(chunk)
        os.replace(tmp, dst)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return {"name": filename, "size": size}
