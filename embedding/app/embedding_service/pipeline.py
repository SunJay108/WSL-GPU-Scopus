"""
Ingestion pipeline: folder -> papers -> chunks -> Qdrant (dense + keyword vectors).

Papers are processed in batches (read, chunk, embed, upload), so memory use
stays the same for ten thousand or ten million papers; the keyword (BM25)
index is part of the Qdrant collection (core/paper_index.py).

Modes:
  append   ("Add to index", the default) adds papers that are not indexed
           yet to the current collection and removes them again if the run
           fails; without an index it builds a new one.
  replace  ("Initialize index") builds a brand-new collection and switches
           the `QDRANT_COLLECTION` alias to it only when everything
           succeeded, so a failed run leaves the current index unchanged.

Qdrant builds the vector index (HNSW) while papers are uploaded.  (Building
it only at the end is slower here: the points then pile up in Qdrant's
write-optimised segments, which it compacts again and again.)

After each run a sample of the new papers is looked up again by title
(keyword and vector search) as a quality check.
"""

import contextlib
import os
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor

from core import embedding, paper_index
from core.config import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    EMBED_BATCH_SIZE,
    EMBED_DEVICE,
    EMBED_FP16,
    EMBED_MODEL_PATH,
    INGEST_BATCH,
    MARIADB_STAGING_DB,
    MIN_ABSTRACT_CHARS,
    OPTIMIZE_TIMEOUT,
    QDRANT_COLLECTION,
    QDRANT_TIMEOUT,
    QDRANT_URL,
    QUALITY_SAMPLE,
)
from core.index_meta import read_index_meta, write_index_meta

from .errors import IngestCancelled, IngestError
from .sources import MariaDBStaging, iter_ris_papers, scan_folder


STAGES = [
    # key, label, weight in the overall progress bar
    ("scan", "Scan folder", 1),
    ("existing", "Read the current index", 3),
    ("stage", "Load MySQL tables", 4),
    ("index", "Read, embed & upload papers", 83),
    ("optimize", "Optimize vector index", 5),
    ("check", "Check search quality", 2),
    ("publish", "Publish index", 2),
]

UPLOAD_STEP = 512        # points per Qdrant upload request
CHARS_PER_TOKEN = 4      # CHUNK_SIZE / CHUNK_OVERLAP are given in tokens
_VERSIONED_COLLECTION = re.compile(rf"^{re.escape(QDRANT_COLLECTION)}_\d{{8}}_\d{{6}}$")


class ConsoleReporter:
    """Progress sink used by the CLI; the web service subclasses this."""

    def __init__(self):
        self.stage = None
        self._last_print = 0.0

    def begin(self, stage, total=None, detail=""):
        self.stage = stage
        print(f"\n▶ {dict((k, l) for k, l, _ in STAGES)[stage]}" + (f" – {detail}" if detail else ""))

    def progress(self, done, total=None, detail=None):
        now = time.time()
        if now - self._last_print >= 2:
            self._last_print = now
            suffix = f"/{total:,}" if total else ""
            print(f"  {done:,}{suffix}" + (f"  {detail}" if detail else ""))

    def finish(self, stage=None, detail=None):
        if detail:
            print(f"  ✓ {detail}")

    def skip(self, stage, reason=""):
        pass

    def count(self, **counters):
        pass

    def log(self, msg, level="info"):
        print(("⚠️ " if level == "warning" else "") + msg)

    def check_cancel(self):
        pass


def run_ingest(folder, tables=None, include_ris=True, mode="append",
               rep=None, staging_db=MARIADB_STAGING_DB, label=None):
    """
    Build or extend the search index from `folder`.

    tables      file stems of the MyISAM tables to import (None = all complete ones)
    include_ris also import *.ris files found in the folder
    mode        "append" (Add to index) or "replace" (Initialize index)
    Returns a summary dict; raises IngestError / IngestCancelled.
    """
    from qdrant_client import QdrantClient

    rep = rep or ConsoleReporter()
    started = time.time()
    version = time.strftime("%Y%m%d_%H%M%S")

    staging = None
    new_collection = None
    appended = False
    published = False
    client = None
    target = None

    try:
        # ---------- 1. scan ----------
        rep.begin("scan", detail=label or folder)
        if not os.path.isdir(folder):
            raise IngestError(f"Folder not found: {label or folder}")

        scan = scan_folder(folder)
        for warning in scan.warnings:
            rep.log(warning, level="warning")

        selected = [t for t in scan.tables if t.complete and (tables is None or t.name in tables)]
        ris_files = scan.ris_files if include_ris else []
        if not selected and not ris_files:
            raise IngestError(
                "Nothing to import: the folder has no complete MySQL tables "
                "(.frm + .MYD + .MYI) or RIS files."
            )

        rep.count(tables=len(selected), ris_files=len(ris_files))
        rep.log(
            f"Found {len(selected)} MySQL table(s)"
            + (f": {', '.join(t.name for t in selected)}" if selected else "")
            + (f" and {len(ris_files)} RIS file(s)" if ris_files else "")
        )

        client = QdrantClient(url=QDRANT_URL, timeout=QDRANT_TIMEOUT)
        try:
            live_collection = resolve_live_collection(client)
        except Exception as exc:
            raise IngestError(f"Cannot reach Qdrant at {QDRANT_URL}: {exc}") from exc

        previous = read_index_meta() or {}
        if mode == "append":
            if live_collection is None:
                rep.log("No existing index found – building a new one instead of appending.")
                mode = "replace"
            elif paper_index.is_legacy(client, live_collection):
                raise IngestError(
                    "The current index was built by an earlier version and cannot be extended. "
                    "Use 'Initialize index' once (then 'Add to index' for further folders)."
                )
        rep.finish("scan")

        # ---------- 2. papers already in the index ----------
        seen = set()
        if mode == "append":
            rep.begin("existing", detail=f"collection {live_collection}")
            seen = indexed_papers(client, live_collection, rep)
            rep.finish("existing", detail=f"{len(seen):,} papers already indexed")
        else:
            rep.skip("existing", "new index")

        # ---------- 3. stage MySQL tables ----------
        if selected:
            staging = MariaDBStaging(staging_db, rep)
            staging.stage(selected)
        else:
            rep.skip("stage", "no MySQL tables")

        # ---------- 4. read, embed, upload (in batches) ----------
        prior_papers = len(seen)
        model = load_embed_model(EMBED_DEVICE, rep)
        indexer = None
        try:
            dim = len(embedding.embed_passages(model, ["dimension probe"])[0])
            if mode == "replace":
                drop_orphan_collections(client, live_collection, rep)
                new_collection = f"{QDRANT_COLLECTION}_{version}"
                paper_index.create_collection(client, new_collection, dim)
                target = new_collection
                avg_tokens = None                      # measured on the first batch
            else:
                target = live_collection
                if paper_index.dense_size(client, target) != dim:
                    raise IngestError(
                        f"The current index uses {paper_index.dense_size(client, target)}-dimensional "
                        f"vectors but the embedding model produces {dim}. Use 'Initialize index' instead."
                    )
                avg_tokens = previous.get("bm25_avg_tokens") or paper_index.DEFAULT_AVG_TOKENS
                appended = True

            indexer = Indexer(client, target, model, version, avg_tokens, rep)
            stats = index_papers(staging, selected, ris_files, seen, indexer, rep)
            # Question vectors for the quality check, while the model is loaded.
            sample = stats["sample"]
            sample_vectors = embedding.embed_queries(indexer.model, [title for title, _ in sample]) if sample else []
        finally:
            if indexer is not None:
                model = indexer.model          # it may have switched to the CPU
                indexer.close()
            embedding.release(model)
        if staging is not None:
            staging.drop()
            staging = None
        if not stats["papers"]:
            if mode == "append" and stats["skipped_duplicate"]:
                raise IngestError("All papers in this folder are already in the index.")
            raise IngestError(
                f"No records with an abstract of at least {MIN_ABSTRACT_CHARS} characters were found."
            )

        # ---------- 5. optimize ----------
        wait_for_index(client, target, rep)

        # ---------- 6. quality check ----------
        quality = check_quality(client, target, sample, sample_vectors, rep)

        # ---------- 7. publish ----------
        rep.begin("publish", detail="Switching to the new index")
        if mode == "replace":
            with_qdrant_retry(lambda: publish_collection(client, target, rep), rep)

        added_at = time.strftime("%Y-%m-%d %H:%M:%S")
        sources = [{"name": name, "papers": n, "added_at": added_at} for name, n in stats["sources"].items()]
        prior_sources = (previous.get("sources") or []) if mode == "append" else []
        points = with_qdrant_retry(lambda: client.get_collection(target).points_count, rep) or 0
        meta = {
            "version": version,
            "built_at": added_at,
            "mode": mode,
            "collection": target,
            "alias": QDRANT_COLLECTION,
            "format": paper_index.INDEX_FORMAT,
            "layout": "dense+bm25",
            "papers": prior_papers + stats["papers"],
            "chunks": points,
            "vectors": points,
            "embed_model": os.path.basename(EMBED_MODEL_PATH.rstrip("/")),
            "embed_dim": dim,
            "bm25_avg_tokens": indexer.avg_tokens,
            "chunk_size": CHUNK_SIZE,
            "chunk_overlap": CHUNK_OVERLAP,
            "min_abstract_chars": MIN_ABSTRACT_CHARS,
            "quality": quality,
            "sources": prior_sources + sources,
        }
        write_index_meta(meta)
        published = True
        rep.finish("publish", detail=f"Index {version} is live")

        summary = {
            "version": version,
            "mode": mode,
            "papers_added": stats["papers"],
            "papers_total": meta["papers"],
            "chunks_total": points,
            "vectors": points,
            "rows_read": stats["rows"],
            "skipped_no_abstract": stats["skipped_no_abstract"],
            "skipped_duplicate": stats["skipped_duplicate"],
            "quality": quality,
            "seconds": round(time.time() - started, 1),
        }
        rep.log(
            f"Done: {summary['papers_added']:,} papers / {len(sources)} source(s) indexed "
            f"in {summary['seconds']:.0f}s."
        )
        return summary

    finally:
        if staging is not None:
            staging.drop()
        if not published:
            _rollback(client, new_collection, target if appended else None, version, rep)


# =====================================================
# Steps
# =====================================================

def indexed_papers(client, collection, rep):
    """Keys of the papers in `collection` (read page by page; only the key field)."""
    seen, offset = set(), None
    total = with_qdrant_retry(lambda: client.get_collection(collection).points_count, rep) or 0
    done = 0
    while True:
        points, offset = with_qdrant_retry(lambda: client.scroll(
            collection, limit=10000, offset=offset, with_payload=["paper"], with_vectors=False), rep)
        for p in points:
            seen.add(p.payload.get("paper"))
        done += len(points)
        rep.progress(done, total)
        rep.check_cancel()
        if offset is None:
            break
    seen.discard(None)
    return seen


def index_papers(staging, tables, ris_files, seen, indexer, rep):
    """Stream the records of all tables and RIS files through the indexer, INGEST_BATCH papers at a time."""
    stats = {"rows": 0, "papers": 0, "skipped_no_abstract": 0, "skipped_duplicate": 0, "sources": {},
             "sample": []}
    totals = {t.name: staging.count_rows(t.name) for t in tables} if tables else {}
    total = sum(totals.values()) or None
    rep.begin("index", total=total, detail=f"on {embedding.device_label(indexer.model)}")
    batch = []
    rng = random.Random(indexer.version)
    candidates = 0           # papers with a usable title, for the quality-check sample

    def sample(paper):
        """Reservoir sample of new papers (title, key), looked up again after the run."""
        nonlocal candidates
        if len(paper["title"]) < 25:
            return
        candidates += 1
        if len(stats["sample"]) < QUALITY_SAMPLE:
            stats["sample"].append((paper["title"], paper["key"]))
        elif rng.random() < QUALITY_SAMPLE / candidates:
            stats["sample"][rng.randrange(QUALITY_SAMPLE)] = (paper["title"], paper["key"])

    def report():
        rep.progress(stats["rows"], total, detail=indexer.speed())
        rep.count(rows=stats["rows"], papers=stats["papers"],
                  skipped_no_abstract=stats["skipped_no_abstract"],
                  skipped_duplicate=stats["skipped_duplicate"],
                  chunks=indexer.chunks, embedded=indexer.chunks)

    def consume(iterator):
        # Closed right away when the run stops early, so the table reader ends
        # its query before the staging database is dropped.
        with contextlib.closing(iterator):
            _consume(iterator)

    def _consume(iterator):
        for paper in iterator:
            stats["rows"] += 1
            if len(paper["abstract"]) < MIN_ABSTRACT_CHARS:
                stats["skipped_no_abstract"] += 1
            else:
                key = paper_index.paper_key(paper["doi"], paper["title"])
                if key in seen:
                    stats["skipped_duplicate"] += 1
                else:
                    seen.add(key)
                    paper["key"] = key
                    batch.append(paper)
                    sample(paper)
                    stats["papers"] += 1
                    stats["sources"][paper["source"]] = stats["sources"].get(paper["source"], 0) + 1
            if len(batch) >= INGEST_BATCH:
                indexer.add(batch)
                batch.clear()
                report()
                rep.check_cancel()

    for table in tables:
        before = stats["rows"]
        rep.log(f"Reading {table.name} ({totals[table.name]:,} rows)...")
        consume(staging.iter_papers(table.name))
        rep.log(f"{table.name}: {stats['rows'] - before:,} rows read")

    for ris_file in ris_files:
        before = stats["rows"]
        rep.log(f"Reading {ris_file.name}...")
        consume(iter_ris_papers(ris_file))
        rep.log(f"{ris_file.name}: {stats['rows'] - before:,} records read")

    if batch:
        indexer.add(batch)
    indexer.flush()
    report()
    rep.finish(
        "index",
        detail=f"{stats['papers']:,} papers ({indexer.chunks:,} chunks) indexed, "
        f"{stats['skipped_no_abstract']:,} without abstract, {stats['skipped_duplicate']:,} duplicates",
    )
    return stats


def check_quality(client, collection, sample, vectors, rep, top=10):
    """
    Look the sampled new papers up again by their titles: with keyword search
    and with vector search each must return the paper among the top 10.
    Low numbers point to a broken index (wrong model, tokenizer or data).
    """
    from qdrant_client.http import models

    if not sample:
        rep.skip("check", "no papers to check")
        return None
    rep.begin("check", total=len(sample), detail=f"looking up {len(sample)} new papers by title")
    keyword = vector = 0
    for n, ((title, key), vector_q) in enumerate(zip(sample, vectors), 1):
        indices, values = paper_index.sparse_query(title)
        hits = with_qdrant_retry(lambda: client.query_points(
            collection, query=models.SparseVector(indices=indices, values=values), using=paper_index.SPARSE,
            limit=top, with_payload=["paper"]).points, rep)
        keyword += any(h.payload.get("paper") == key for h in hits)
        hits = with_qdrant_retry(lambda: client.query_points(
            collection, query=vector_q, using=paper_index.DENSE, limit=top, with_payload=["paper"]).points, rep)
        vector += any(h.payload.get("paper") == key for h in hits)
        rep.progress(n, len(sample))
        rep.check_cancel()
    result = {"sample": len(sample), "keyword_found": keyword, "vector_found": vector, "top": top}
    text = (f"{keyword}/{len(sample)} found by keyword search, {vector}/{len(sample)} by vector search "
            f"(title among the top {top})")
    if min(keyword, vector) < 0.8 * len(sample):
        rep.log(f"Quality check: only {text} – check the data and the embedding model.", "warning")
    else:
        rep.log(f"Quality check: {text}.")
    rep.finish("check", detail=text)
    return result


class Indexer:
    """
    Chunks a batch of papers, embeds it on the GPU and uploads it to Qdrant;
    the upload of one batch overlaps with embedding the next.  At most two
    batches are in memory.
    """

    def __init__(self, client, collection, model, version, avg_tokens, rep):
        self.client, self.collection, self.model = client, collection, model
        self.version, self.avg_tokens, self.rep = version, avg_tokens, rep
        self.encode_batch = EMBED_BATCH_SIZE
        self.max_chars = CHUNK_SIZE * CHARS_PER_TOKEN
        self.overlap_chars = CHUNK_OVERLAP * CHARS_PER_TOKEN
        self.chunks = 0
        self.started = time.time()
        self.uploader = ThreadPoolExecutor(max_workers=1)
        self.pending = None

    def add(self, papers):
        items = [(p, n, text) for p in papers
                 for n, text in enumerate(paper_index.chunk_paper(p, self.max_chars, self.overlap_chars))]
        if self.avg_tokens is None:   # first batch of a new index: average document length
            lengths = [len(paper_index.tokenize(paper_index.keyword_text(p, t))) for p, _, t in items]
            self.avg_tokens = round(sum(lengths) / max(1, len(lengths)), 1)
            self.rep.log(f"Keyword index: average passage length {self.avg_tokens} words")
        vectors = self._embed([text for _, _, text in items])     # the GPU part
        # Keyword vectors and the upload run in the background while the GPU
        # embeds the next batch.
        self.flush()
        self.pending = self.uploader.submit(self._upload, items, vectors)
        self.chunks += len(items)

    def _embed(self, texts):
        while True:
            try:
                return embedding.embed_passages(self.model, texts, batch_size=self.encode_batch)
            except Exception as exc:
                if not _is_cuda_oom(exc):
                    raise
                self._handle_oom()

    def _handle_oom(self):
        """Halve the batch size; below 8 continue on the CPU."""
        import torch
        torch.cuda.empty_cache()
        if self.encode_batch > 8:
            self.encode_batch //= 2
            self.rep.log(f"GPU out of memory – retrying with batch size {self.encode_batch}", "warning")
            return
        self.rep.log("GPU out of memory even at batch size 8 – continuing on CPU", "warning")
        embedding.release(self.model)
        self.model = embedding.load("cpu", fp16=False)

    def _upload(self, items, vectors):
        from qdrant_client.http import models

        points = []
        for (paper, n, text), vector in zip(items, vectors):
            indices, values = paper_index.sparse_document(paper_index.keyword_text(paper, text), self.avg_tokens)
            points.append(models.PointStruct(
                id=paper_index.point_id(paper["key"], n),
                vector={paper_index.DENSE: vector,
                        paper_index.SPARSE: models.SparseVector(indices=indices, values=values)},
                payload={"text": text, "title": paper["title"], "doi": paper["doi"], "year": paper["year"],
                         "journal": paper["journal"], "source": paper["source"], "paper": paper["key"],
                         "chunk": n, "batch": self.version},
            ))
        for start in range(0, len(points), UPLOAD_STEP):
            part = points[start:start + UPLOAD_STEP]
            with_qdrant_retry(lambda: self.client.upsert(self.collection, points=part, wait=True), self.rep)

    def flush(self):
        """Wait for the previous upload (and raise its error, if any)."""
        if self.pending is not None:
            self.pending.result()
            self.pending = None

    def speed(self):
        rate = self.chunks / max(time.time() - self.started, 1e-6)
        return f"{self.chunks:,} chunks · {rate:,.0f}/s on {embedding.device_label(self.model)}"

    def close(self):
        try:
            self.flush()
        finally:
            self.uploader.shutdown(wait=True)


def wait_for_index(client, collection, rep):
    from qdrant_client.http.models import CollectionStatus

    rep.begin("optimize", detail="Waiting for Qdrant to finish the vector index")
    deadline = time.time() + OPTIMIZE_TIMEOUT
    time.sleep(2)  # give the optimizer a moment to pick up new segments
    green_polls = 0

    while True:
        info = with_qdrant_retry(lambda: client.get_collection(collection), rep)
        points = info.points_count or 0
        # The count includes the keyword vectors (always indexed); the rest are
        # dense vectors in segments that already have their HNSW graph.
        indexed = min(max((info.indexed_vectors_count or 0) - points, 0), points)
        rep.progress(indexed, points or None, detail=f"{indexed:,} / {points:,} vectors in the HNSW index")

        green_polls = green_polls + 1 if info.status == CollectionStatus.GREEN else 0
        if green_polls >= 2:
            break
        if time.time() > deadline:
            rep.log("Qdrant is still optimizing; publishing anyway (searches already work).", "warning")
            break

        rep.check_cancel()
        time.sleep(1)

    rep.finish("optimize", detail=f"{points:,} vectors ready")


# =====================================================
# Qdrant helpers
# =====================================================

def _models():
    from qdrant_client.http import models
    return models


def resolve_live_collection(client):
    """Name of the collection behind the `QDRANT_COLLECTION` alias (the current index), or None."""
    for alias in client.get_aliases().aliases:
        if alias.alias_name == QDRANT_COLLECTION:
            return alias.collection_name
    if client.collection_exists(QDRANT_COLLECTION):
        return QDRANT_COLLECTION  # layout from before the embedding service
    return None


def with_qdrant_retry(fn, rep, timeout=180):
    """Run a Qdrant call, waiting out a short Qdrant restart instead of failing the job."""
    from qdrant_client.http.exceptions import ResponseHandlingException

    deadline = time.time() + timeout
    warned = False
    while True:
        try:
            return fn()
        except (ResponseHandlingException, ConnectionError):
            if time.time() > deadline:
                raise
            if not warned:
                rep.log("Qdrant is not reachable – waiting for it to come back...", "warning")
                warned = True
            rep.check_cancel()
            time.sleep(3)


def publish_collection(client, new_name, rep):
    models = _models()
    aliases = {a.alias_name: a.collection_name for a in client.get_aliases().aliases}
    operations = []
    old = aliases.get(QDRANT_COLLECTION)

    if old:
        operations.append(models.DeleteAliasOperation(
            delete_alias=models.DeleteAlias(alias_name=QDRANT_COLLECTION)))
    elif client.collection_exists(QDRANT_COLLECTION):
        # A real collection holds the alias name (index built by the old ingest.py).
        rep.log(f"Removing legacy collection '{QDRANT_COLLECTION}'")
        client.delete_collection(QDRANT_COLLECTION)

    operations.append(models.CreateAliasOperation(
        create_alias=models.CreateAlias(collection_name=new_name, alias_name=QDRANT_COLLECTION)))
    client.update_collection_aliases(change_aliases_operations=operations)
    rep.log(f"Alias '{QDRANT_COLLECTION}' now points to '{new_name}'")

    # Drop the previous index and leftovers of cancelled/failed runs.
    for collection in client.get_collections().collections:
        name = collection.name
        if name != new_name and (name == old or _VERSIONED_COLLECTION.match(name)):
            client.delete_collection(name)
            rep.log(f"Deleted old collection '{name}'")


def drop_orphan_collections(client, keep, rep):
    """Delete collections left behind by runs that crashed before cleaning up."""
    for collection in client.get_collections().collections:
        if collection.name != keep and _VERSIONED_COLLECTION.match(collection.name):
            client.delete_collection(collection.name)
            rep.log(f"Deleted leftover collection '{collection.name}' from an unfinished run")


def _rollback(client, new_collection, appended_to, version, rep):
    """Undo an unfinished run: drop its new collection, or the points it appended."""
    if client is None:
        return
    models = _models()
    try:
        if new_collection and client.collection_exists(new_collection):
            client.delete_collection(new_collection)
            rep.log(f"Discarded unfinished collection '{new_collection}'; the current index is unchanged.")
        if appended_to:
            client.delete(collection_name=appended_to, points_selector=models.FilterSelector(
                filter=models.Filter(must=[models.FieldCondition(
                    key="batch", match=models.MatchValue(value=version))])))
            rep.log("Removed the partially appended papers; the index is as before.")
    except Exception as exc:
        rep.log(f"Cleanup after failure was incomplete: {exc}", level="warning")


# =====================================================
# Embedding model helpers
# =====================================================

def load_embed_model(requested, rep):
    import torch

    device = embedding.resolve_device(requested)
    rep.log(f"Loading embedding model {os.path.basename(EMBED_MODEL_PATH.rstrip('/'))} on {device}...")
    try:
        model = embedding.load(device, fp16=EMBED_FP16)
        embedding.embed_passages(model, ["warm-up"])
        return model
    except Exception as exc:
        if device == "cpu" or not _is_cuda_oom(exc):
            raise
        torch.cuda.empty_cache()
        rep.log(
            "Not enough free GPU memory for the embedding model – embedding on CPU instead. "
            "Close other programs that use the GPU (see nvidia-smi) for a fast ingest.",
            "warning",
        )
        return load_embed_model("cpu", rep)


def _is_cuda_oom(exc):
    text = f"{type(exc).__name__} {exc}".lower()
    return "out of memory" in text or "outofmemory" in text or "cublas_status_alloc_failed" in text
