"""
Import of index bundles made by the embedding app (embedding/ in this
repository, on its own PC).  This is how the search app gets a new index.

    BUNDLES_DIR/<bundle>/  papers.snapshot  index_meta.json  manifest.json  SHA256SUMS

An import
  1. checks the bundle: its index format and embedding model must be the
     ones of this app (core/paper_index.INDEX_FORMAT, EMBED_MODEL_PATH), and
     its files complete and undamaged (sizes and SHA-256);
  2. lets Qdrant load the snapshot into a new collection - vectors, keyword
     index and texts, nothing is rebuilt - and checks it: number of chunks,
     vector layout, one vector search and one keyword search;
  3. replace (default): switches the alias QDRANT_COLLECTION to the new
     collection, writes index_meta.json and deletes the previous collection;
     add: copies the smaller of the new collection and the current index
     into the other one, makes that one the index and adds the bundle to
     index_meta.json.

Replace: searches use the current index until step 3, and a failed import
leaves it unchanged.  Meanwhile Qdrant holds both indexes (memory and disk for
two).  With low_memory the current index is deleted before step 2 instead:
half the memory, but no search during the import and no index if it fails.

Add: point ids are made from the paper key (paper_index.point_id), so a paper
that is already in the index is replaced by the bundle's copy, never
duplicated, and Qdrant applies the keyword IDF at query time, so nothing else
has to be recomputed.  Copying takes about 20 s per 1,000 chunks on a hard
disk, so only the smaller side is copied:
  - a bundle with fewer chunks than the index is copied into the index in use.
    Searches find the added papers while they are copied.  A failed add takes
    its new papers out again, but papers it was replacing can stay incomplete
    until the bundle is added again.
  - otherwise the index is copied into the new collection (skipping papers the
    bundle has too), which then replaces it as in a replace: searches use the
    current index until the switch, a failed add leaves it unchanged, and
    Qdrant holds both meanwhile.
Without a current index, an add is a replace.

One import at a time, also between the admin panel and import_index.py.
"""

import contextlib
import fcntl
import hashlib
import json
import os
import re
import shutil
import threading
import time
import uuid

from core import paper_index
from core.config import (
    BUNDLES_DIR,
    EMBED_MODEL_PATH,
    IMPORT_TIMEOUT,
    INDEX_META_PATH,
    QDRANT_BUNDLES_DIR,
    QDRANT_COLLECTION,
    QDRANT_URL,
    STORAGE_PATH,
)
from core.index_meta import read_index_meta, write_index_meta

BUNDLE_FORMAT = "scopus-index-bundle"
BUNDLE_VERSIONS = (1,)
LOCK_PATH = os.path.join(STORAGE_PATH, ".import.lock")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_VERSION = re.compile(r"^\d{8}_\d{6}$")


class BundleError(RuntimeError):
    """The bundle cannot be imported (the current index is unchanged)."""


class ImportBusyError(RuntimeError):
    pass


# =====================================================
# Bundles
# =====================================================

def embed_model_name():
    return os.path.basename(EMBED_MODEL_PATH.rstrip("/"))


def embed_model_dim():
    """Vector size of this app's embedding model, from its files (None if unknown)."""
    for name, key in (("1_Pooling/config.json", "word_embedding_dimension"), ("config.json", "hidden_size")):
        try:
            with open(os.path.join(EMBED_MODEL_PATH, name), encoding="utf-8") as f:
                return int(json.load(f)[key])
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return None


def bundle_path(name):
    if not _NAME.match(name or ""):
        raise BundleError("Invalid bundle name.")
    path = os.path.join(BUNDLES_DIR, name)
    if not os.path.isfile(os.path.join(path, "manifest.json")):
        raise BundleError(f"No bundle '{name}' in the bundles folder (manifest.json missing).")
    return path


def read_manifest(path):
    try:
        with open(os.path.join(path, "manifest.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as exc:
        raise BundleError(f"manifest.json cannot be read: {exc}")


def _version_tuple(text):
    return tuple(int(x) for x in re.findall(r"\d+", text or "")[:2]) or (0, 0)


def check_manifest(manifest, qdrant_version=None):
    """Problems that rule the bundle out (empty list = compatible)."""
    problems = []
    if manifest.get("bundle") != BUNDLE_FORMAT:
        return ["not an index bundle of the embedding app"]
    if manifest.get("bundle_version") not in BUNDLE_VERSIONS:
        problems.append(f"bundle version {manifest.get('bundle_version')} is not supported by this search app")
    if manifest.get("format") != paper_index.INDEX_FORMAT:
        problems.append(f"index format '{manifest.get('format')}' differs from this app's "
                        f"'{paper_index.INDEX_FORMAT}' (update the app that is older)")
    if manifest.get("embed_model") != embed_model_name():
        problems.append(f"built with the embedding model '{manifest.get('embed_model')}', "
                        f"this app uses '{embed_model_name()}'")
    dim = embed_model_dim()
    if dim and manifest.get("embed_dim") != dim:
        problems.append(f"{manifest.get('embed_dim')}-dimensional vectors, this app's model makes {dim}")
    if not _VERSION.match(str(manifest.get("index_version") or "")):
        problems.append("index version missing in manifest.json")
    if qdrant_version and _version_tuple(manifest.get("qdrant_version")) > _version_tuple(qdrant_version):
        problems.append(f"made with Qdrant {manifest.get('qdrant_version')}, newer than this app's Qdrant "
                        f"{qdrant_version}")
    if "papers.snapshot" not in (manifest.get("files") or {}):
        problems.append("the snapshot is not listed in manifest.json")
    return problems


def check_files(path, manifest):
    """Missing files or wrong sizes (fast; the checksums are checked by verify_checksums)."""
    problems = []
    for name, expected in (manifest.get("files") or {}).items():
        file_path = os.path.join(path, name)
        if os.path.basename(name) != name:
            problems.append(f"{name}: invalid file name")
        elif not os.path.isfile(file_path):
            problems.append(f"{name} is missing")
        elif os.path.getsize(file_path) != expected.get("size"):
            problems.append(f"{name} is incomplete ({os.path.getsize(file_path):,} of "
                            f"{expected.get('size'):,} bytes; copy it again)")
    return problems


def verify_checksums(path, manifest, progress=None):
    """SHA-256 of every file; progress(done_bytes, total_bytes)."""
    files = manifest.get("files") or {}
    total = sum(f.get("size", 0) for f in files.values())
    done = 0
    for name, expected in files.items():
        digest = hashlib.sha256()
        with open(os.path.join(path, name), "rb") as f:
            for block in iter(lambda: f.read(8 * 2**20), b""):
                digest.update(block)
                done += len(block)
                if progress:
                    progress(done, total)
        if digest.hexdigest() != expected.get("sha256"):
            raise BundleError(f"{name} is damaged (SHA-256 does not match manifest.json); copy the bundle again.")


def list_bundles():
    """Bundles in BUNDLES_DIR, newest first, with what would prevent their import."""
    in_index = index_versions(read_index_meta())
    try:
        qdrant_version = _qdrant(timeout=5).info().version
    except Exception:
        qdrant_version = None
    bundles = []
    try:
        names = sorted(os.listdir(BUNDLES_DIR))
    except FileNotFoundError:
        return bundles
    for name in names:
        path = os.path.join(BUNDLES_DIR, name)
        if name.startswith(".") or not os.path.isfile(os.path.join(path, "manifest.json")):
            continue
        try:
            manifest = read_manifest(path)
            problems = check_manifest(manifest, qdrant_version) + check_files(path, manifest)
        except BundleError as exc:
            manifest, problems = {}, [str(exc)]
        bundles.append({
            "name": name,
            "created_at": manifest.get("created_at"),
            "index_version": manifest.get("index_version"),
            "papers": manifest.get("papers"),
            "chunks": manifest.get("chunks"),
            "size": sum(f.get("size", 0) for f in (manifest.get("files") or {}).values()),
            "problems": problems,
            "in_use": not problems and manifest.get("index_version") in in_index,
        })
    return sorted(bundles, key=lambda b: b["created_at"] or "", reverse=True)


def delete_bundle(name):
    path = bundle_path(name)
    shutil.rmtree(path)


# =====================================================
# Import
# =====================================================

# Points per request when an add copies points between collections, and
# Qdrant's own time limit for reading one page (60 s by default; a hard disk
# needs 6 s or more per 1,000 points with their texts).
COPY_PAGE = 1000
COPY_READ_TIMEOUT = 600


def _qdrant(timeout=IMPORT_TIMEOUT):
    from qdrant_client import QdrantClient
    return QdrantClient(url=QDRANT_URL, timeout=timeout)


@contextlib.contextmanager
def exclusive():
    """Held during an import; a second import (web UI or command line) gets ImportBusyError."""
    os.makedirs(os.path.dirname(LOCK_PATH), exist_ok=True)
    with open(LOCK_PATH, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ImportBusyError("An index import is already running.")
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def live_collection(client):
    for alias in client.get_aliases().aliases:
        if alias.alias_name == QDRANT_COLLECTION:
            return alias.collection_name
    if client.collection_exists(QDRANT_COLLECTION):
        return QDRANT_COLLECTION          # a collection (not an alias) of an early version
    return None


def _qdrant_error(exc):
    """The message of a Qdrant error response instead of the client's raw dump."""
    try:
        return json.loads(exc.content)["status"]["error"]
    except Exception:
        return str(exc).splitlines()[0]


def _wait_until_green(client, collection, report):
    from qdrant_client.http.models import CollectionStatus

    deadline = time.time() + IMPORT_TIMEOUT
    green = 0
    while green < 2:
        status = client.get_collection(collection).status
        green = green + 1 if status == CollectionStatus.GREEN else 0
        if green == 0:
            if time.time() > deadline:
                raise BundleError("Qdrant did not finish loading the collection in time.")
            report("Qdrant is finishing the loaded index…")
        time.sleep(1)


def _check_collection(client, collection, manifest):
    """The loaded collection must hold the bundle's chunks and answer a vector and a keyword search."""
    from qdrant_client.http import models

    info = client.get_collection(collection)
    if info.points_count != manifest.get("chunks"):
        raise BundleError(f"The loaded index has {info.points_count:,} chunks, the bundle {manifest.get('chunks'):,}.")
    if paper_index.is_legacy(client, collection):
        raise BundleError("The loaded index does not have the vector + keyword layout.")
    if paper_index.dense_size(client, collection) != manifest.get("embed_dim"):
        raise BundleError("The loaded index has a different vector size than manifest.json.")
    points, _ = client.scroll(collection, limit=1, with_payload=["paper"], with_vectors=True)
    if not points:
        raise BundleError("The loaded index is empty.")
    point = points[0]
    dense = point.vector[paper_index.DENSE]
    sparse = point.vector[paper_index.SPARSE]
    hits = client.query_points(collection, query=dense, using=paper_index.DENSE, limit=5).points
    if point.id not in [h.id for h in hits]:
        raise BundleError("Vector search on the loaded index does not find a stored passage.")
    hits = client.query_points(collection, using=paper_index.SPARSE, limit=5, query=models.SparseVector(
        indices=sparse.indices, values=sparse.values)).points
    if point.id not in [h.id for h in hits]:
        raise BundleError("Keyword search on the loaded index does not find a stored passage.")


def _switch_alias(client, collection):
    from qdrant_client.http import models

    operations = []
    if any(a.alias_name == QDRANT_COLLECTION for a in client.get_aliases().aliases):
        operations.append(models.DeleteAliasOperation(delete_alias=models.DeleteAlias(alias_name=QDRANT_COLLECTION)))
    elif client.collection_exists(QDRANT_COLLECTION):
        client.delete_collection(QDRANT_COLLECTION)    # early version: a collection named like the alias
    operations.append(models.CreateAliasOperation(
        create_alias=models.CreateAlias(collection_name=collection, alias_name=QDRANT_COLLECTION)))
    # One request: searches see either the old or the new index, never none.
    client.update_collection_aliases(change_aliases_operations=operations)


def _remove_live_index(client, live):
    from qdrant_client.http import models

    if live != QDRANT_COLLECTION:
        client.update_collection_aliases(change_aliases_operations=[
            models.DeleteAliasOperation(delete_alias=models.DeleteAlias(alias_name=QDRANT_COLLECTION))])
    client.delete_collection(live)
    with contextlib.suppress(FileNotFoundError):
        os.remove(INDEX_META_PATH)


def index_versions(meta):
    """Index versions of the bundles in the live index: the imported one and the ones added to it."""
    meta = meta or {}
    versions = {meta.get("version")} | {a.get("version") for a in meta.get("added") or []}
    versions.discard(None)
    return versions


def import_bundle(name, low_memory=False, report=None, username="", add=False):
    """
    Import bundle `name` from BUNDLES_DIR; returns a summary.  Raises BundleError / ImportBusyError.
    add=False: the bundle replaces the current index; add=True: its papers are added to it.
    """
    report = report or (lambda step, done=None, total=None: print(step))
    started = time.time()
    with exclusive():
        path, manifest, client = _open_bundle(name, report)
        live = live_collection(client)
        if add and live:
            summary = _add(client, name, path, manifest, live, report, username)
        else:
            summary = _replace(client, name, path, manifest, live, low_memory, report, username)
    summary["seconds"] = round(time.time() - started)
    return summary


def _open_bundle(name, report):
    """Bundle `name`, completely checked (manifest, files, SHA-256): (path, manifest, Qdrant client)."""
    path = bundle_path(name)
    manifest = read_manifest(path)
    client = _qdrant()
    try:
        qdrant_version = client.info().version
    except Exception as exc:
        raise BundleError(f"Qdrant is not reachable at {QDRANT_URL}: {exc}")
    problems = check_manifest(manifest, qdrant_version) + check_files(path, manifest)
    if problems:
        raise BundleError("The bundle cannot be imported: " + "; ".join(problems) + ".")

    report("Checking the files (SHA-256)…", 0, 1)
    verify_checksums(path, manifest, lambda done, total: report("Checking the files (SHA-256)…", done, total))
    return path, manifest, client


def _drop_unused_collections(client, live):
    """Collections of earlier imports or ingests that are not in use."""
    for c in client.get_collections().collections:
        if c.name != live and re.match(rf"^{re.escape(QDRANT_COLLECTION)}_\d{{8}}_\d{{6}}$", c.name):
            client.delete_collection(c.name)


def _load_snapshot(client, name, manifest, target, report):
    """Qdrant loads the bundle's snapshot into the new collection `target`, which is then checked."""
    from qdrant_client.http import models
    from qdrant_client.http.exceptions import UnexpectedResponse

    snapshot = f"file://{QDRANT_BUNDLES_DIR.rstrip('/')}/{name}/papers.snapshot"
    size_gb = manifest["files"]["papers.snapshot"]["size"] / 2**30
    report(f"Qdrant is loading the snapshot ({size_gb:.1f} GB; about {max(1, round(size_gb * 1.5))} min "
           "on a hard disk)…")
    try:
        client.recover_snapshot(target, location=snapshot, priority=models.SnapshotPriority.SNAPSHOT, wait=True)
    except UnexpectedResponse as exc:
        raise BundleError(f"Qdrant could not load the snapshot: {_qdrant_error(exc)}.") from exc
    _wait_until_green(client, target, report)
    report("Checking the loaded index…")
    _check_collection(client, target, manifest)


def _bundle_meta(path, manifest):
    """The bundle's index description (its index_meta.json, else the summary in manifest.json)."""
    try:
        with open(os.path.join(path, "index_meta.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        meta = {k: manifest.get(k) for k in ("papers", "chunks", "embed_model", "embed_dim", "format")}
        meta["version"] = manifest["index_version"]
        return meta


def _replace(client, name, path, manifest, live, low_memory, report, username):
    """The bundle becomes the index (steps 2 and 3 of the module description)."""
    target = f"{QDRANT_COLLECTION}_{manifest['index_version']}"
    if target == live and not low_memory:
        raise BundleError("This index is already in use.")
    _drop_unused_collections(client, live)

    removed_live = False
    if low_memory and live:
        report("Removing the current index to free memory…")
        _remove_live_index(client, live)
        removed_live, live = True, None

    try:
        _load_snapshot(client, name, manifest, target, report)
        report("Switching to the new index…")
        _switch_alias(client, target)
    except BaseException as exc:
        with contextlib.suppress(Exception):
            if client.collection_exists(target):
                client.delete_collection(target)
        if removed_live and isinstance(exc, Exception):
            raise BundleError(f"{exc} The previous index had been removed (low-memory import); "
                              "import a bundle again.") from exc
        raise

    meta = _bundle_meta(path, manifest)
    meta.update({"collection": target, "alias": QDRANT_COLLECTION, "bundle": name,
                 "imported_at": time.strftime("%Y-%m-%d %H:%M:%S"), "imported_by": username})
    write_index_meta(meta)

    if live and live != target:
        report("Deleting the previous index…")
        with contextlib.suppress(Exception):
            client.delete_collection(live)

    return {"mode": "replace", "bundle": name, "version": manifest["index_version"],
            "papers": manifest.get("papers"), "chunks": manifest.get("chunks"), "collection": target,
            "previous": live}


def _add(client, name, path, manifest, live, report, username):
    """The bundle's papers are added to the current index `live` (steps 2 and 3 of the module description)."""
    meta = read_index_meta() or {}
    version = manifest["index_version"]
    staging = f"{QDRANT_COLLECTION}_{version}"
    if version in index_versions(meta) or staging == live:
        raise BundleError("This bundle is already in the index.")
    if paper_index.is_legacy(client, live):
        raise BundleError("The current index has the layout of an earlier version and cannot be extended; "
                          "replace it with a bundle instead.")
    if paper_index.dense_size(client, live) != manifest.get("embed_dim"):
        raise BundleError("The current index has a different vector size than the bundle; "
                          "replace it with a bundle instead.")
    _drop_unused_collections(client, live)

    # Only the smaller side is copied: a smaller bundle into the current index, or the current
    # index into a larger bundle, which then becomes the index (like a replace).
    into_bundle = (manifest.get("chunks") or 0) > client.count(live, exact=True).count
    loaded = switched = False
    try:
        _load_snapshot(client, name, manifest, staging, report)
        loaded = True
        if into_bundle:
            _, replaced = _copy_points(client, live, staging, report, "Copying the current index into the "
                                       "loaded bundle…", source_wins=False)
            new = max(0, (manifest.get("papers") or 0) - replaced)
            report("Switching to the new index…")
            _switch_alias(client, staging)
            switched = True
        else:
            new, replaced = _copy_points(client, staging, live, report, "Adding the bundle's papers to the "
                                         "current index…")
    except Exception as exc:
        if loaded and into_bundle:
            raise BundleError(f"Adding the papers failed: {_qdrant_error(exc)}. The current index is "
                              "unchanged.") from exc
        if loaded:
            raise BundleError(f"Adding the papers failed: {_qdrant_error(exc)}. Papers of the bundle that were "
                              "already in the index can be incomplete; add the bundle again.") from exc
        raise
    finally:
        if not switched:
            with contextlib.suppress(Exception):
                if client.collection_exists(staging):
                    report("Removing the loaded copy of the bundle…")
                    client.delete_collection(staging)

    previous = None
    if switched:
        previous, live = live, staging
        meta["collection"] = staging
        if previous != QDRANT_COLLECTION:     # a collection named like the alias is gone already
            report("Deleting the previous index…")
            with contextlib.suppress(Exception):
                client.delete_collection(previous)

    chunks = client.count(live, exact=True).count
    known = {(s.get("name"), s.get("added_at")) for s in meta.get("sources") or []}
    sources = [s for s in _bundle_meta(path, manifest).get("sources") or []
               if (s.get("name"), s.get("added_at")) not in known]
    if meta.get("papers") is not None:
        meta["papers"] += new
    meta.setdefault("collection", live)
    meta.setdefault("alias", QDRANT_COLLECTION)
    meta.update({
        "chunks": chunks,
        "vectors": chunks,
        "sources": (meta.get("sources") or []) + sources,
        "added": (meta.get("added") or []) + [{
            "bundle": name, "version": version, "papers": manifest.get("papers"), "papers_new": new,
            "papers_replaced": replaced, "chunks": manifest.get("chunks"),
            "added_at": time.strftime("%Y-%m-%d %H:%M:%S"), "added_by": username}],
    })
    write_index_meta(meta)

    return {"mode": "add", "bundle": name, "version": version, "papers": manifest.get("papers"),
            "papers_new": new, "papers_replaced": replaced, "chunks": manifest.get("chunks"),
            "collection": live, "previous": previous, "index_papers": meta.get("papers"),
            "index_chunks": chunks}


def _copy_points(client, source, target, report, step, source_wins=True):
    """
    Copy the points of `source` into `target`, COPY_PAGE points at a time.  For a paper that is in
    both, source_wins=True first deletes its points in `target` (the source may have fewer chunks
    of it); source_wins=False keeps the points in `target` and skips the source's.
    Returns (papers of `source` that were not in `target`, papers that were in both).
    """
    from qdrant_client.http import models

    def papers(keys):
        return models.FilterSelector(filter=models.Filter(must=[
            models.FieldCondition(key="paper", match=models.MatchAny(any=list(keys)))]))

    total = client.count(source, exact=True).count
    # Paper key -> whether it was in `target` before the copy, decided at the first of its chunks
    # (the chunks of one paper are spread over the pages).
    in_target = {}
    added = []                       # papers that were not in `target`
    done = 0
    offset = None
    try:
        while True:
            points, offset = client.scroll(source, limit=COPY_PAGE, offset=offset, with_payload=True,
                                           with_vectors=True, timeout=COPY_READ_TIMEOUT)
            keys = {p.payload.get("paper") for p in points} - in_target.keys() - {None}
            if keys:
                # Every paper has a chunk 0; looking it up by id reads nothing from disk.
                first = {paper_index.point_id(key, 0): key for key in keys}
                present = {first[str(uuid.UUID(str(p.id)))] for p in client.retrieve(
                    target, ids=list(first), with_payload=False, with_vectors=False)}
                if present and source_wins:
                    client.delete(target, wait=True, points_selector=papers(present))
                in_target.update((key, key in present) for key in keys)
                added.extend(keys - present)
            copy = points if source_wins else [p for p in points if not in_target.get(p.payload.get("paper"))]
            if copy:
                client.upsert(target, wait=True, points=[
                    models.PointStruct(id=p.id, vector=p.vector, payload=p.payload) for p in copy])
            done += len(points)
            report(step, done, total)
            if offset is None:
                return len(added), len(in_target) - len(added)
    except BaseException:
        if source_wins:
            # Copied into the index in use: take the new papers out again, so that adding the
            # bundle again counts them as new.
            with contextlib.suppress(Exception):
                for start in range(0, len(added), COPY_PAGE):
                    client.delete(target, wait=True, points_selector=papers(added[start:start + COPY_PAGE]))
        raise


class ImportManager:
    """Runs one import at a time in a background thread (admin panel)."""

    def __init__(self, on_success=None):
        self._lock = threading.Lock()
        self.state = {"state": "idle"}
        self.on_success = on_success

    def snapshot(self):
        with self._lock:
            return dict(self.state)

    def running(self):
        return self.snapshot().get("state") == "running"

    def start(self, name, user, low_memory=False, add=False):
        with self._lock:
            if self.state.get("state") == "running":
                raise ImportBusyError("An index import is already running.")
            self.state = {"state": "running", "bundle": name, "low_memory": low_memory, "add": add,
                          "step": "Starting…", "started_at": time.time(), "started_by": user["username"]}
        threading.Thread(target=self._run, args=(name, user, low_memory, add), name="index-import",
                         daemon=True).start()

    def _report(self, step, done=None, total=None):
        with self._lock:
            self.state.update(step=step, done=done, total=total)

    def _run(self, name, user, low_memory, add):
        from core import audit
        try:
            result = import_bundle(name, low_memory, self._report, user["username"], add=add)
        except Exception as exc:
            with self._lock:
                self.state.update(state="failed", finished_at=time.time(), error=str(exc), step="Failed")
            audit.log("index_import_failed", user, detail={"bundle": name, "error": str(exc)})
            return
        with self._lock:
            self.state.update(state="completed", finished_at=time.time(), result=result, step="Finished",
                              done=None, total=None)
        audit.log("index_import_completed", user, detail=result)
        if self.on_success:
            threading.Thread(target=self.on_success, name="index-warm-up", daemon=True).start()
