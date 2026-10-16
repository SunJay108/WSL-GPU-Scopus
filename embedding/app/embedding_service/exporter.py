"""
Export bundles: everything the search app needs to use the current index.

    <EXPORT_DIR>/papers_<index version>_<export time>/
        papers.snapshot   Qdrant snapshot of the collection (vectors, keyword index, texts)
        index_meta.json   description of the index (papers, sources, model, format)
        manifest.json     bundle format, sizes and SHA-256 checksums
        SHA256SUMS        the same checksums for `sha256sum -c SHA256SUMS`
        README.txt        what the folder is and how to use it

Copy the whole folder to the search PC.  Qdrant writes the snapshot into
QDRANT_SNAPSHOTS_DIR (inside EXPORT_DIR), from where it is moved into the
bundle without copying.  A bundle is complete once manifest.json exists.
"""

import hashlib
import json
import os
import shutil
import threading
import time

from core import paper_index
from core.config import EXPORT_DIR, OPTIMIZE_TIMEOUT, QDRANT_COLLECTION, QDRANT_SNAPSHOTS_DIR, QDRANT_URL
from core.index_meta import atomic_write_json, read_index_meta

BUNDLE_FORMAT = "scopus-index-bundle"
BUNDLE_VERSION = 1
SNAPSHOT_FILE = "papers.snapshot"

README = """Scopus AI search index bundle
=============================

Created by the embedding service on {created_at}.
Index version {version}: {papers:,} papers, {chunks:,} chunks, format {format}.

Copy this whole folder into the bundles folder of the search app on the
search PC (search/bundles by default), then import it there: admin panel >
Index > Import, or
  docker exec -it scopus-ai-gpu python import_index.py {name}
Do not change or rename the files: the search app checks them against
manifest.json (SHA-256) before importing.

To check a copy yourself:
  Linux / WSL:  sha256sum -c SHA256SUMS
  Windows:      Get-FileHash papers.snapshot   (compare with SHA256SUMS)
"""


class ExportBusyError(RuntimeError):
    pass


class ExportManager:
    """Runs one export at a time in a background thread."""

    def __init__(self):
        self._lock = threading.Lock()
        self.state = {"state": "idle"}

    def snapshot(self):
        with self._lock:
            return dict(self.state)

    def running(self):
        return self.snapshot().get("state") == "running"

    def start(self, user):
        with self._lock:
            if self.state.get("state") == "running":
                raise ExportBusyError("An export is already running.")
            self.state = {"state": "running", "step": "Starting…", "started_at": time.time(),
                          "started_by": user["username"]}
        threading.Thread(target=self._run, args=(user,), name="export", daemon=True).start()

    def _set(self, **fields):
        with self._lock:
            self.state.update(fields)

    def _run(self, user):
        from core import audit
        try:
            bundle = export_bundle(lambda step: self._set(step=step), user["username"])
            self._set(state="completed", finished_at=time.time(), bundle=bundle["name"], step="Finished")
            audit.log("export_completed", user, detail={"bundle": bundle["name"], "size": bundle["size"]})
        except Exception as exc:
            self._set(state="failed", finished_at=time.time(), error=str(exc), step="Failed")
            audit.log("export_failed", user, detail={"error": str(exc)})


def _wait_until_indexed(client, collection, report):
    """The snapshot must contain the finished vector index: wait until Qdrant is idle (green)."""
    from qdrant_client.http.models import CollectionStatus

    deadline = time.time() + OPTIMIZE_TIMEOUT
    green = 0
    while green < 2:
        info = client.get_collection(collection)
        green = green + 1 if info.status == CollectionStatus.GREEN else 0
        if green == 0:
            if time.time() > deadline:
                raise RuntimeError("Qdrant is still building the vector index. Export again later.")
            report("Waiting for Qdrant to finish building the vector index…")
        time.sleep(1)


def _sha256(path, chunk=8 * 2**20):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def export_bundle(report, username=""):
    """Create a bundle of the current index; returns its description."""
    from qdrant_client import QdrantClient

    meta = read_index_meta()
    if not meta:
        raise RuntimeError("There is no index to export yet. Run an ingest first.")
    if meta.get("format") != paper_index.INDEX_FORMAT:
        raise RuntimeError("The index was built by an older version; initialize it again before exporting.")

    # Snapshots of large collections take minutes: no short HTTP timeout.
    client = QdrantClient(url=QDRANT_URL, timeout=7200)
    collection = next((a.collection_name for a in client.get_aliases().aliases
                       if a.alias_name == QDRANT_COLLECTION), None)
    if collection is None:
        raise RuntimeError("The index collection was not found in Qdrant.")
    _wait_until_indexed(client, collection, report)
    points = client.get_collection(collection).points_count

    created = time.strftime("%Y%m%d_%H%M%S")
    name = f"papers_{meta['version']}_{created}"
    partial = os.path.join(EXPORT_DIR, f".{name}.partial")
    final = os.path.join(EXPORT_DIR, name)
    os.makedirs(partial, exist_ok=True)
    try:
        report(f"Qdrant is writing the snapshot of {points:,} chunks…")
        snap = client.create_snapshot(collection_name=collection, wait=True)
        source = os.path.join(QDRANT_SNAPSHOTS_DIR, collection, snap.name)
        if not os.path.exists(source):
            raise RuntimeError(f"Snapshot {snap.name} not found in {QDRANT_SNAPSHOTS_DIR}.")
        target = os.path.join(partial, SNAPSHOT_FILE)
        os.replace(source, target)
        try:
            os.remove(source + ".checksum")
        except OSError:
            pass

        report("Computing checksums…")
        checksum = getattr(snap, "checksum", None) or _sha256(target)
        atomic_write_json(os.path.join(partial, "index_meta.json"), meta)
        files = {
            SNAPSHOT_FILE: {"size": os.path.getsize(target), "sha256": checksum},
            "index_meta.json": {"size": os.path.getsize(os.path.join(partial, "index_meta.json")),
                                "sha256": _sha256(os.path.join(partial, "index_meta.json"))},
        }
        try:
            qdrant_version = client.info().version
        except Exception:
            qdrant_version = None
        created_at = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(os.path.join(partial, "README.txt"), "w", encoding="utf-8") as f:
            f.write(README.format(created_at=created_at, version=meta["version"], papers=meta.get("papers") or 0,
                                  chunks=meta.get("chunks") or points, format=meta["format"], name=name))
        with open(os.path.join(partial, "SHA256SUMS"), "w", encoding="utf-8") as f:
            f.writelines(f"{info['sha256']}  {name}\n" for name, info in files.items())
        manifest = {
            "bundle": BUNDLE_FORMAT,
            "bundle_version": BUNDLE_VERSION,
            "name": name,
            "created_at": created_at,
            "created_by": username,
            "index_version": meta["version"],
            "format": meta["format"],
            "collection_alias": QDRANT_COLLECTION,
            "qdrant_version": qdrant_version,
            "embed_model": meta.get("embed_model"),
            "embed_dim": meta.get("embed_dim"),
            "papers": meta.get("papers"),
            "chunks": points,
            "files": files,
        }
        # Written last: a bundle with manifest.json is complete.
        atomic_write_json(os.path.join(partial, "manifest.json"), manifest)
        os.chmod(target, 0o644)
        os.replace(partial, final)
        return {"name": name, "size": sum(f["size"] for f in files.values())}
    except BaseException:
        shutil.rmtree(partial, ignore_errors=True)
        raise


def list_bundles():
    """Complete bundles in EXPORT_DIR, newest first."""
    bundles = []
    try:
        names = os.listdir(EXPORT_DIR)
    except FileNotFoundError:
        return bundles
    for name in names:
        path = os.path.join(EXPORT_DIR, name)
        manifest_path = os.path.join(path, "manifest.json")
        if name.startswith(".") or not os.path.isfile(manifest_path):
            continue
        try:
            with open(manifest_path, encoding="utf-8") as f:
                manifest = json.load(f)
        except (OSError, ValueError):
            continue
        bundles.append({
            "name": name,
            "created_at": manifest.get("created_at"),
            "index_version": manifest.get("index_version"),
            "papers": manifest.get("papers"),
            "chunks": manifest.get("chunks"),
            "size": sum(f.get("size", 0) for f in (manifest.get("files") or {}).values()),
        })
    return sorted(bundles, key=lambda b: b["created_at"] or "", reverse=True)


def delete_bundle(name):
    path = os.path.realpath(os.path.join(EXPORT_DIR, name))
    if os.path.dirname(path) != os.path.realpath(EXPORT_DIR) or not os.path.isfile(os.path.join(path, "manifest.json")):
        raise FileNotFoundError(name)
    shutil.rmtree(path)


def verify_bundle(path, report=print):
    """Check a bundle's files against its manifest; returns a list of problems (empty = OK)."""
    try:
        with open(os.path.join(path, "manifest.json"), encoding="utf-8") as f:
            manifest = json.load(f)
    except (OSError, ValueError) as exc:
        return [f"manifest.json: {exc}"]
    problems = []
    if manifest.get("bundle") != BUNDLE_FORMAT:
        problems.append("manifest.json: not a Scopus index bundle")
    for name, expected in (manifest.get("files") or {}).items():
        file_path = os.path.join(path, name)
        if not os.path.isfile(file_path):
            problems.append(f"{name}: missing")
            continue
        if os.path.getsize(file_path) != expected.get("size"):
            problems.append(f"{name}: size {os.path.getsize(file_path):,} instead of {expected.get('size'):,}")
            continue
        report(f"Checking {name} ({expected['size'] / 2**30:.2f} GB)…")
        if _sha256(file_path) != expected.get("sha256"):
            problems.append(f"{name}: SHA-256 does not match (damaged copy)")
    return problems


def cleanup_partial():
    """Remove bundles left unfinished by a restart, and snapshots Qdrant wrote for them."""
    for name in os.listdir(EXPORT_DIR) if os.path.isdir(EXPORT_DIR) else []:
        if name.startswith(".") and name.endswith(".partial"):
            shutil.rmtree(os.path.join(EXPORT_DIR, name), ignore_errors=True)
    for collection in os.listdir(QDRANT_SNAPSHOTS_DIR) if os.path.isdir(QDRANT_SNAPSHOTS_DIR) else []:
        folder = os.path.join(QDRANT_SNAPSHOTS_DIR, collection)
        if collection.startswith(".") or not os.path.isdir(folder):
            continue                      # Qdrant's own temporary folder
        for name in os.listdir(folder):
            if name.endswith((".snapshot", ".snapshot.checksum")):
                try:
                    os.remove(os.path.join(folder, name))
                except OSError:
                    pass
