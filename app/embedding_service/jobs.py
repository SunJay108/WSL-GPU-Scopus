"""
Background ingest job with progress tracking.

Only one job runs at a time.  Each job runs in its own child process, so all
GPU memory (including the CUDA context) and the several GB of RAM used while
building BM25 are returned to the system when it ends; on a small GPU the
Scopus UI's LLM needs that memory.  The child streams progress events through
a queue; the server process keeps the job state for the UI (/api/status) and
persists it to EMBED_JOB_DIR/last_job.json so a browser reload or container
restart still shows how the last run ended.
"""

import multiprocessing as mp
import os
import queue as queue_mod
import threading
import time
import traceback
from collections import deque

from config import EMBED_JOB_DIR
from index_meta import atomic_write_json

from .errors import IngestCancelled, IngestError
from .pipeline import STAGES, ConsoleReporter, run_ingest


STATE_PATH = os.path.join(EMBED_JOB_DIR, "last_job.json")
TERMINAL_STATES = {"completed", "failed", "cancelled", "interrupted"}


class JobBusyError(RuntimeError):
    pass


class Job:
    def __init__(self, request: dict):
        self.id = time.strftime("%Y%m%d-%H%M%S")
        self.request = request
        self.state = "running"
        self.started_at = time.time()
        self.finished_at = None
        self.stage = None
        self.stages = [
            {"key": key, "label": label, "weight": weight, "state": "pending",
             "done": 0, "total": None, "detail": "", "started_at": None, "finished_at": None}
            for key, label, weight in STAGES
        ]
        self.counters = {}
        self.logs = deque(maxlen=1000)
        self.log_seq = 0
        self.error = None
        self.summary = None
        self.cancel_requested = False

    def stage_entry(self, key):
        return next(s for s in self.stages if s["key"] == key)

    def percent(self):
        if self.state == "completed":
            return 100.0
        active = [s for s in self.stages if s["state"] != "skipped"]
        total_weight = sum(s["weight"] for s in active) or 1
        progress = 0.0
        for s in active:
            if s["state"] == "done":
                progress += s["weight"]
            elif s["state"] in ("running", "failed") and s["total"]:
                progress += s["weight"] * min(s["done"] / s["total"], 1.0)
        return round(100.0 * progress / total_weight, 1)

    def eta_seconds(self):
        """Rough ETA, driven by the embedding stage which dominates the runtime."""
        if self.state != "running" or self.stage != "embed":
            return None
        s = self.stage_entry("embed")
        if not s["total"] or not s["done"] or not s["started_at"]:
            return None
        rate = s["done"] / max(time.time() - s["started_at"], 1e-6)
        return round((s["total"] - s["done"]) / rate + 15)  # + optimize/publish

    def to_dict(self, log_since=0):
        end = self.finished_at or time.time()
        return {
            "id": self.id,
            "state": self.state,
            "request": {k: v for k, v in self.request.items() if k != "folder"},
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "elapsed": round(end - self.started_at, 1),
            "percent": self.percent(),
            "eta": self.eta_seconds(),
            "stage": self.stage,
            "stages": [{k: v for k, v in s.items() if k != "weight"} for s in self.stages],
            "counters": dict(self.counters),
            "logs": [entry for entry in self.logs if entry["seq"] > log_since],
            "log_seq": self.log_seq,
            "error": self.error,
            "summary": self.summary,
            "cancel_requested": self.cancel_requested,
        }

    @classmethod
    def from_dict(cls, data: dict):
        job = cls(data.get("request") or {})
        job.id = data.get("id", job.id)
        job.state = data.get("state", "interrupted")
        job.started_at = data.get("started_at") or job.started_at
        job.finished_at = data.get("finished_at")
        job.stage = data.get("stage")
        for saved in data.get("stages") or []:
            for s in job.stages:
                if s["key"] == saved.get("key"):
                    s.update({k: v for k, v in saved.items() if k in s})
        job.counters = data.get("counters") or {}
        for entry in data.get("logs") or []:
            job.logs.append(entry)
        job.log_seq = data.get("log_seq", len(job.logs))
        job.error = data.get("error")
        job.summary = data.get("summary")
        return job


# =====================================================
# Child process side
# =====================================================

class QueueReporter(ConsoleReporter):
    """Runs in the worker: prints like the CLI and forwards events to the server."""

    def __init__(self, events, cancel_event):
        super().__init__()
        self.events = events
        self.cancel_event = cancel_event

    def begin(self, stage, total=None, detail=""):
        super().begin(stage, total, detail)
        self.events.put(("begin", stage, total, detail))

    def progress(self, done, total=None, detail=None):
        super().progress(done, total, detail)
        self.events.put(("progress", done, total, detail))

    def finish(self, stage=None, detail=None):
        super().finish(stage, detail)
        self.events.put(("finish", stage, detail))

    def skip(self, stage, reason=""):
        self.events.put(("skip", stage, reason))

    def count(self, **counters):
        self.events.put(("count", counters))

    def log(self, msg, level="info"):
        super().log(msg, level)
        self.events.put(("log", msg, level))

    def check_cancel(self):
        if self.cancel_event.is_set():
            raise IngestCancelled()


def _worker(request, events, cancel_event):
    rep = QueueReporter(events, cancel_event)
    try:
        summary = run_ingest(
            folder=request["folder"],
            tables=request.get("tables"),
            include_ris=request.get("include_ris", True),
            mode=request["mode"],
            rep=rep,
            label=request["label"],
        )
        events.put(("done", summary))
    except IngestCancelled:
        events.put(("cancelled",))
    except IngestError as exc:
        events.put(("error", str(exc)))
    except BaseException as exc:
        traceback.print_exc()
        events.put(("error", f"{type(exc).__name__}: {exc}"))


# =====================================================
# Server side
# =====================================================

class JobState:
    """Applies worker events to a Job (thread-safe)."""

    def __init__(self, manager, job):
        self.manager = manager
        self.job = job

    def _update(self, fn, throttle=False):
        with self.manager.lock:
            fn(self.job)
        self.manager.persist(throttle=throttle)

    def begin(self, stage, total=None, detail=""):
        def apply(job):
            if job.stage and job.stage != stage:
                prev = job.stage_entry(job.stage)
                if prev["state"] == "running":
                    prev["state"] = "done"
                    prev["finished_at"] = time.time()
            job.stage = stage
            job.stage_entry(stage).update(
                state="running", done=0, total=total, detail=detail, started_at=time.time())

        self._update(apply)

    def progress(self, done, total=None, detail=None):
        def apply(job):
            s = job.stage_entry(job.stage)
            if total is not None:
                s["total"] = total
            s["done"] = min(done, s["total"]) if s["total"] else done
            if detail is not None:
                s["detail"] = detail

        self._update(apply, throttle=True)

    def finish(self, stage=None, detail=None):
        def apply(job):
            s = job.stage_entry(stage or job.stage)
            s["state"] = "done"
            s["finished_at"] = time.time()
            if s["total"]:
                s["done"] = s["total"]
            if detail is not None:
                s["detail"] = detail

        self._update(apply)

    def skip(self, stage, reason=""):
        self._update(lambda job: job.stage_entry(stage).update(state="skipped", detail=reason))

    def count(self, counters):
        with self.manager.lock:
            self.job.counters.update(counters)

    def log(self, msg, level="info"):
        def apply(job):
            job.log_seq += 1
            job.logs.append({"seq": job.log_seq, "t": time.time(), "level": level, "msg": msg})

        self._update(apply, throttle=True)


class JobManager:
    def __init__(self):
        self.lock = threading.RLock()
        self.job = None
        self._cancel_event = None
        self._last_persist = 0.0
        self._load()

    # ---------- public API ----------

    def start(self, request: dict) -> Job:
        with self.lock:
            if self.job is not None and self.job.state == "running":
                raise JobBusyError("An ingest job is already running.")
            job = Job(request)
            self.job = job

        threading.Thread(target=self._run, args=(job,), name=f"ingest-{job.id}", daemon=True).start()
        return job

    def cancel(self) -> bool:
        with self.lock:
            if self.job is None or self.job.state != "running":
                return False
            self.job.cancel_requested = True
            if self._cancel_event is not None:
                self._cancel_event.set()
        JobState(self, self.job).log("Cancel requested – stopping after the current step...", "warning")
        return True

    def snapshot(self, log_since=0):
        with self.lock:
            return self.job.to_dict(log_since) if self.job else None

    # ---------- internals ----------

    def _run(self, job: Job):
        state = JobState(self, job)
        req = job.request
        state.log(f"Ingest started: folder '{req['label']}', mode '{req['mode']}'")

        ctx = mp.get_context("spawn")
        events = ctx.Queue()
        cancel_event = ctx.Event()
        with self.lock:
            self._cancel_event = cancel_event
            if job.cancel_requested:
                cancel_event.set()

        proc = ctx.Process(target=_worker, args=(req, events, cancel_event),
                           name=f"ingest-worker-{job.id}", daemon=True)
        proc.start()

        outcome = None
        while outcome is None:
            try:
                event = events.get(timeout=1.0)
            except queue_mod.Empty:
                if not proc.is_alive():
                    outcome = ("error", f"The ingest worker stopped unexpectedly (exit code {proc.exitcode}). "
                                        "See 'docker logs scopus-embedding'.")
                continue

            kind, args = event[0], event[1:]
            if kind in ("done", "cancelled", "error"):
                outcome = event
            elif kind == "count":
                state.count(*args)
            else:
                getattr(state, kind)(*args)

        proc.join(timeout=30)

        kind = outcome[0]
        status = {"done": "completed", "cancelled": "cancelled", "error": "failed"}[kind]
        if kind == "cancelled":
            state.log("Ingest cancelled. The current index was left unchanged.", "warning")
        elif kind == "error":
            state.log(outcome[1], "error")

        with self.lock:
            job.state = status
            job.error = outcome[1] if kind == "error" else None
            job.summary = outcome[1] if kind == "done" else None
            job.finished_at = time.time()
            self._cancel_event = None
            if job.stage:
                current = job.stage_entry(job.stage)
                if current["state"] == "running":
                    current["state"] = "done" if status == "completed" else "failed"
                    current["finished_at"] = job.finished_at
        self.persist()

    def persist(self, throttle=False):
        now = time.time()
        if throttle and now - self._last_persist < 2:
            return
        self._last_persist = now
        with self.lock:
            if self.job is None:
                return
            data = self.job.to_dict()
        try:
            atomic_write_json(STATE_PATH, data)
        except OSError as exc:
            print(f"⚠️ Could not save job state: {exc}")

    def _load(self):
        import json

        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                job = Job.from_dict(json.load(f))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            print(f"⚠️ Ignoring unreadable {STATE_PATH}: {exc}")
            return

        if job.state not in TERMINAL_STATES:
            # The service stopped while this job was running.
            job.state = "interrupted"
            job.error = "The embedding service was restarted while this job was running."
            job.finished_at = job.finished_at or time.time()
        self.job = job
