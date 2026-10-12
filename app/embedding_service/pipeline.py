"""
Ingestion pipeline: folder -> papers -> chunks -> BM25 + Qdrant vectors.

Replace mode builds a brand-new Qdrant collection and only switches the
`QDRANT_COLLECTION` alias to it once everything succeeded, so the Scopus UI
keeps answering from the old index during the whole run.  Append mode adds
papers whose DOI is not indexed yet to the live collection.
"""

import gc
import os
import pickle
import re
import time
from concurrent.futures import ThreadPoolExecutor

from core.config import (
    BM25_PATH,
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    EMBED_BATCH_SIZE,
    EMBED_DEVICE,
    EMBED_FP16,
    EMBED_MODEL_PATH,
    MARIADB_STAGING_DB,
    MIN_ABSTRACT_CHARS,
    OPTIMIZE_TIMEOUT,
    QDRANT_COLLECTION,
    QDRANT_TIMEOUT,
    QDRANT_URL,
)
from core.index_meta import read_index_meta, write_index_meta

from .errors import IngestCancelled, IngestError
from .sources import MariaDBStaging, iter_ris_papers, scan_folder


STAGES = [
    # key, label, weight in the overall progress bar
    ("scan", "Scan folder", 1),
    ("stage", "Load MySQL tables", 4),
    ("read", "Read records", 6),
    ("chunk", "Split into chunks", 6),
    ("bm25", "Build keyword index (BM25)", 8),
    ("embed", "Embed & upload vectors", 68),
    ("optimize", "Optimize vector index", 5),
    ("publish", "Publish index", 2),
]

UPLOAD_STEP = 256   # points per Qdrant upload request
EMBED_STEP = 1024   # chunks per encode call; sentence-transformers length-sorts within it
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


def run_ingest(folder, tables=None, include_ris=True, mode="replace",
               rep=None, staging_db=MARIADB_STAGING_DB, label=None):
    """
    Build or extend the search index from `folder`.

    tables      file stems of the MyISAM tables to import (None = all complete ones)
    include_ris also import *.ris files found in the folder
    mode        "replace" or "append"
    Returns a summary dict; raises IngestError / IngestCancelled.
    """
    from qdrant_client import QdrantClient

    rep = rep or ConsoleReporter()
    started = time.time()
    version = time.strftime("%Y%m%d_%H%M%S")
    bm25_tmp = BM25_PATH + ".new"

    staging = None
    new_collection = None
    appended_ids = []
    published = False
    client = None

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

        existing_docs = []
        if mode == "append":
            if live_collection is None or not os.path.exists(BM25_PATH):
                rep.log("No existing index found – building a new one instead of appending.")
                mode = "replace"
            else:
                rep.log("Loading the current keyword index to append to it...")
                with open(BM25_PATH, "rb") as f:
                    _, existing_docs = pickle.load(f)
        rep.finish("scan")

        # ---------- 2. stage MySQL tables ----------
        if selected:
            staging = MariaDBStaging(staging_db, rep)
            staging.stage(selected)
        else:
            rep.skip("stage", "no MySQL tables")

        # ---------- 3. read records ----------
        papers, stats = read_papers(staging, selected, ris_files, existing_docs, rep)
        if staging is not None:
            staging.drop()
            staging = None
        if not papers:
            if mode == "append" and stats["skipped_duplicate"]:
                raise IngestError("All papers in this folder are already in the index.")
            raise IngestError(
                f"No records with an abstract of at least {MIN_ABSTRACT_CHARS} characters were found."
            )

        # ---------- 4. chunk ----------
        nodes = build_nodes(papers, rep)
        sources = summarize_sources(papers)
        del papers

        # ---------- 5. BM25 ----------
        new_docs = [{"node_id": n.node_id, "text": n.text, "metadata": n.metadata} for n in nodes]
        corpus = existing_docs + new_docs
        build_bm25(corpus, bm25_tmp, rep)
        total_chunks = len(corpus)
        total_papers = count_papers(corpus)
        prior_papers = count_papers(existing_docs)
        del corpus, existing_docs, new_docs
        gc.collect()

        # ---------- 6. embed ----------
        embed_model = load_embed_model(EMBED_DEVICE, rep)
        try:
            dim = len(embed_model.get_text_embedding("dimension probe"))

            if mode == "replace":
                drop_orphan_collections(client, live_collection, rep)
                new_collection = f"{QDRANT_COLLECTION}_{version}"
                create_collection(client, new_collection, dim)
                target = new_collection
            else:
                target = live_collection
                live_dim = collection_dim(client, target)
                if live_dim != dim:
                    raise IngestError(
                        f"The current index uses {live_dim}-dimensional vectors but the "
                        f"embedding model produces {dim}. Use 'Replace' mode instead."
                    )

            embed_and_upload(
                nodes, embed_model, client, target, rep,
                appended_ids if mode == "append" else None,
            )
        finally:
            release_model(embed_model)
        del nodes

        # ---------- 7. optimize ----------
        wait_for_index(client, target, rep)

        # ---------- 8. publish ----------
        rep.begin("publish", detail="Switching the Scopus UI to the new index")
        if mode == "replace":
            with_qdrant_retry(lambda: publish_collection(client, target, rep), rep)
        os.replace(bm25_tmp, BM25_PATH)

        prior_sources = []
        if mode == "append":
            previous = read_index_meta() or {}
            prior_sources = previous.get("sources") or [
                {"name": "existing index", "papers": prior_papers, "added_at": None}
            ]
        points = with_qdrant_retry(lambda: client.get_collection(target).points_count, rep) or 0
        meta = {
            "version": version,
            "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "mode": mode,
            "collection": target,
            "alias": QDRANT_COLLECTION,
            "papers": total_papers,
            "chunks": total_chunks,
            "vectors": points,
            "embed_model": os.path.basename(EMBED_MODEL_PATH.rstrip("/")),
            "embed_dim": dim,
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
            "chunks_total": total_chunks,
            "vectors": points,
            "rows_read": stats["rows"],
            "skipped_no_abstract": stats["skipped_no_abstract"],
            "skipped_duplicate": stats["skipped_duplicate"],
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
            _rollback(client, new_collection, appended_ids, rep)
            try:
                os.remove(bm25_tmp)
            except OSError:
                pass


# =====================================================
# Steps
# =====================================================

def read_papers(staging, tables, ris_files, existing_docs, rep):
    stats = {"rows": 0, "papers": 0, "skipped_no_abstract": 0, "skipped_duplicate": 0}
    seen = {
        (d["metadata"].get("doi") or "").lower()
        for d in existing_docs
    }
    seen.discard("")
    papers = []

    totals = {t.name: staging.count_rows(t.name) for t in tables} if tables else {}
    total = sum(totals.values()) or None
    rep.begin("read", total=total)

    def consume(iterator):
        for paper in iterator:
            stats["rows"] += 1
            if len(paper["abstract"]) < MIN_ABSTRACT_CHARS:
                stats["skipped_no_abstract"] += 1
            else:
                key = paper["doi"].lower()
                if key and key in seen:
                    stats["skipped_duplicate"] += 1
                else:
                    if key:
                        seen.add(key)
                    papers.append(paper)

            if stats["rows"] % 2000 == 0:
                rep.progress(stats["rows"], total)
                rep.count(rows=stats["rows"], papers=len(papers),
                          skipped_no_abstract=stats["skipped_no_abstract"],
                          skipped_duplicate=stats["skipped_duplicate"])
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

    stats["papers"] = len(papers)
    rep.count(rows=stats["rows"], papers=len(papers),
              skipped_no_abstract=stats["skipped_no_abstract"],
              skipped_duplicate=stats["skipped_duplicate"])
    rep.finish(
        "read",
        detail=f"{len(papers):,} papers kept, {stats['skipped_no_abstract']:,} without abstract, "
        f"{stats['skipped_duplicate']:,} duplicates",
    )
    return papers, stats


def build_weighted_text(p):
    """Same layout as the original RIS ingest: title and keywords are repeated as a boost."""
    parts = []

    if p["title"]:
        parts.append("Title: " + p["title"])
        parts.append("Title: " + p["title"])

    if p["keywords"]:
        kw = ", ".join(p["keywords"])
        parts.append("Keywords: " + kw)
        parts.append("Keywords: " + kw)

    if p["authors"]:
        parts.append("Authors: " + ", ".join(p["authors"]))

    if p["journal"]:
        parts.append("Journal: " + p["journal"])

    if p["year"]:
        parts.append("Year: " + p["year"])

    if p["abstract"]:
        parts.append("Abstract:\n" + p["abstract"])

    return "\n\n".join(parts)


def build_nodes(papers, rep, batch=500):
    _use_bundled_nltk_data()
    from llama_index.core import Document
    from llama_index.core.node_parser import SentenceSplitter

    splitter = SentenceSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)
    rep.begin("chunk", total=len(papers))

    nodes = []
    for start in range(0, len(papers), batch):
        docs = [
            Document(
                text=build_weighted_text(p),
                metadata={
                    "title": p["title"][:300],
                    "doi": p["doi"],
                    "year": p["year"],
                    "journal": p["journal"][:200],
                    "source": p["source"],
                },
                # Traceability only: keep it out of the embedded / LLM text.
                excluded_embed_metadata_keys=["source"],
                excluded_llm_metadata_keys=["source"],
            )
            for p in papers[start:start + batch]
        ]
        nodes.extend(splitter.get_nodes_from_documents(docs))

        done = min(start + batch, len(papers))
        rep.progress(done, len(papers), detail=f"{len(nodes):,} chunks")
        rep.count(chunks=len(nodes))
        rep.check_cancel()

    rep.finish("chunk", detail=f"{len(nodes):,} chunks from {len(papers):,} papers")
    return nodes


def build_bm25(corpus, path, rep):
    from rank_bm25 import BM25Okapi

    rep.begin("bm25", total=len(corpus), detail="Tokenizing")
    tokenized = []
    for i, doc in enumerate(corpus, 1):
        # Must match the tokenisation in query.py.
        tokenized.append(doc["text"].lower().split())
        if i % 20000 == 0:
            rep.progress(i, len(corpus), detail="Tokenizing")
            rep.check_cancel()

    rep.progress(len(corpus), len(corpus), detail="Computing term statistics")
    bm25 = BM25Okapi(tokenized)
    del tokenized
    rep.check_cancel()

    rep.progress(len(corpus), len(corpus), detail="Saving")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        pickle.dump((bm25, corpus), f, protocol=pickle.HIGHEST_PROTOCOL)

    rep.finish("bm25", detail=f"{len(corpus):,} chunks, {os.path.getsize(path) / 1e6:,.0f} MB")


def embed_and_upload(nodes, embed_model, client, collection, rep, appended_ids=None):
    """Embed on the GPU while the previous batch uploads to Qdrant."""
    from llama_index.core.schema import MetadataMode
    from llama_index.vector_stores.qdrant import QdrantVectorStore

    vector_store = QdrantVectorStore(
        client=client,
        collection_name=collection,
        batch_size=UPLOAD_STEP,
    )

    total = len(nodes)
    rep.begin("embed", total=total, detail=f"on {_device_label(embed_model)}")
    step = max(EMBED_BATCH_SIZE, EMBED_STEP)

    def upload(batch):
        vector_store.add(batch)
        if appended_ids is not None:
            appended_ids.extend(n.node_id for n in batch)
        for n in batch:
            n.embedding = None  # free memory once stored in Qdrant

    pending = None
    done = 0
    started = time.time()

    with ThreadPoolExecutor(max_workers=1) as uploader:
        while done < total:
            rep.check_cancel()
            batch = nodes[done:done + step]
            # Same text the original VectorStoreIndex embedded: metadata header + chunk.
            texts = [n.get_content(metadata_mode=MetadataMode.EMBED) for n in batch]

            try:
                vectors = embed_model.get_text_embedding_batch(texts)
            except Exception as exc:
                if not _is_cuda_oom(exc):
                    raise
                embed_model = _handle_oom(embed_model, rep)
                continue

            for node, vector in zip(batch, vectors):
                node.embedding = vector

            if pending is not None:
                pending.result()
            pending = uploader.submit(upload, batch)

            done += len(batch)
            elapsed = max(time.time() - started, 1e-6)
            rate = done / elapsed
            rep.progress(done, total, detail=f"{rate:,.0f} chunks/s on {_device_label(embed_model)}")
            rep.count(embedded=done)

        if pending is not None:
            pending.result()

    rep.finish("embed", detail=f"{total:,} vectors uploaded")


def wait_for_index(client, collection, rep):
    from qdrant_client.http.models import CollectionStatus

    rep.begin("optimize", detail="Waiting for Qdrant to build the HNSW index")
    deadline = time.time() + OPTIMIZE_TIMEOUT
    time.sleep(2)  # give the optimizer a moment to pick up new segments
    green_polls = 0

    while True:
        info = with_qdrant_retry(lambda: client.get_collection(collection), rep)
        points = info.points_count or 0
        indexed = min(info.indexed_vectors_count or 0, points)
        rep.progress(indexed, points or None, detail=f"{indexed:,} / {points:,} vectors indexed")

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
    """Name of the collection the Scopus UI currently searches, or None."""
    for alias in client.get_aliases().aliases:
        if alias.alias_name == QDRANT_COLLECTION:
            return alias.collection_name
    if client.collection_exists(QDRANT_COLLECTION):
        return QDRANT_COLLECTION  # layout from before the embedding service
    return None


def collection_dim(client, name):
    vectors = client.get_collection(name).config.params.vectors
    return vectors.size if hasattr(vectors, "size") else next(iter(vectors.values())).size


def create_collection(client, name, dim):
    models = _models()
    # Same parameters as the original ingest.  Qdrant indexes progressively
    # while the (much slower) embedding runs, so the final optimize is short.
    client.create_collection(
        collection_name=name,
        vectors_config=models.VectorParams(size=dim, distance=models.Distance.COSINE),
        hnsw_config=models.HnswConfigDiff(m=32, ef_construct=256),
    )


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


def _rollback(client, new_collection, appended_ids, rep):
    if client is None:
        return
    try:
        if new_collection and client.collection_exists(new_collection):
            client.delete_collection(new_collection)
            rep.log(f"Discarded unfinished collection '{new_collection}'; the current index is unchanged.")
        if appended_ids:
            live = resolve_live_collection(client)
            if live:
                client.delete(
                    collection_name=live,
                    points_selector=_models().PointIdsList(points=appended_ids),
                )
                rep.log(f"Removed {len(appended_ids):,} partially appended vectors.")
    except Exception as exc:
        rep.log(f"Cleanup after failure was incomplete: {exc}", level="warning")


# =====================================================
# Embedding model helpers
# =====================================================

def load_embed_model(requested, rep):
    import torch
    from llama_index.embeddings.huggingface import HuggingFaceEmbedding

    device = (requested or "cpu").strip().lower()
    if device.startswith("cuda") and not torch.cuda.is_available():
        rep.log("CUDA requested but unavailable; embedding on CPU (much slower).", "warning")
        device = "cpu"

    rep.log(f"Loading embedding model {os.path.basename(EMBED_MODEL_PATH.rstrip('/'))} on {device}...")
    try:
        # Same class and defaults as query.py, so query and document vectors match.
        model = HuggingFaceEmbedding(
            model_name=EMBED_MODEL_PATH,
            device=device,
            embed_batch_size=EMBED_BATCH_SIZE,
        )
        if device.startswith("cuda") and EMBED_FP16:
            # ~4x faster on consumer GPUs; vectors match fp32 to ~1e-3 cosine.
            model._model.half()
        model.get_text_embedding("warm-up")
        return model
    except Exception as exc:
        if device == "cpu" or not _is_cuda_oom(exc):
            raise
        model = None
        gc.collect()
        torch.cuda.empty_cache()
        rep.log(
            "Not enough free GPU memory for the embedding model (the Scopus UI's LLM "
            "uses most of it) – embedding on CPU instead. To use the GPU for large "
            "ingests, stop the search UI first: docker compose stop scopus-ai",
            "warning",
        )
        return load_embed_model("cpu", rep)


def release_model(model):
    del model
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def _device_label(embed_model):
    label = str(getattr(embed_model, "_device", "?"))
    try:
        import torch
        if next(embed_model._model.parameters()).dtype == torch.float16:
            label += " (fp16)"
    except Exception:
        pass
    return label


def _is_cuda_oom(exc):
    text = f"{type(exc).__name__} {exc}".lower()
    return "out of memory" in text or "outofmemory" in text or "cublas_status_alloc_failed" in text


def _handle_oom(embed_model, rep):
    """Halve the batch size; below 8 fall back to the CPU."""
    import torch
    torch.cuda.empty_cache()

    if embed_model.embed_batch_size > 8:
        embed_model.embed_batch_size //= 2
        rep.log(f"GPU out of memory – retrying with batch size {embed_model.embed_batch_size}", "warning")
        return embed_model

    rep.log("GPU out of memory even at batch size 8 – continuing on CPU", "warning")
    release_model(embed_model)
    return load_embed_model("cpu", rep)


def _use_bundled_nltk_data():
    """
    SentenceSplitter needs NLTK stopwords/punkt.  llama-index ships them in its
    package but looks under $NLTK_DATA (or ~/.cache) and otherwise downloads
    them from GitHub, which fails on the offline PC.
    """
    import llama_index.core

    os.environ.setdefault("NLTK_DATA", os.path.dirname(llama_index.core.__file__))


def count_papers(docs):
    """Distinct papers in a BM25 corpus (a paper may span several chunks)."""
    keys = set()
    for d in docs:
        md = d["metadata"]
        keys.add((md.get("doi") or "").lower() or "title:" + (md.get("title") or ""))
    return len(keys)


def summarize_sources(papers):
    counts = {}
    for p in papers:
        counts[p["source"]] = counts.get(p["source"], 0) + 1
    added = time.strftime("%Y-%m-%d %H:%M:%S")
    return [{"name": name, "papers": n, "added_at": added} for name, n in counts.items()]
