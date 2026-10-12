"""
Answering pipeline: hybrid retrieval (Qdrant vectors + BM25, RRF fusion,
cross-encoder rerank), grounded generation by the language model server
(llm.py), summary, citations and follow-up suggestions.  Reloads the index
when the embedding service publishes a new one.

Answers contain only what the retrieved papers say: the model writes from
numbered sources, and every sentence is checked against them before it is
shown (grounding.py); unsupported sentences are dropped.  Up to LLM_PARALLEL
questions are answered at the same time.
"""

import os
import pickle
import re
import threading
import time

import torch

from llama_index.core import VectorStoreIndex
from llama_index.core.postprocessor import SentenceTransformerRerank
from llama_index.core.schema import NodeWithScore, TextNode
from llama_index.embeddings.huggingface import HuggingFaceEmbedding
from llama_index.vector_stores.qdrant import QdrantVectorStore
from qdrant_client import QdrantClient

from core.config import *
from core.index_meta import index_signature, read_index_meta
from . import grounding, llm, safety
from .filters import find_hard_keyword
from .followup import standalone_question
from .guard import check_question


# =====================================================
# Global singletons
# =====================================================

_embed_model = None
_reranker = None

# Rebuilt whenever the embedding service publishes a new index.
_retriever = None
_bm25_index = None
_bm25_corpus = None
_index_signature = None

_load_lock = threading.RLock()
# The embedding model and reranker are shared by all questions; their
# tokenizers must not be used by two threads at once.
_model_lock = threading.Lock()
# Questions answered at the same time (= parallel slots of the model server).
_answer_slots = threading.BoundedSemaphore(LLM_PARALLEL)


class IndexNotReady(RuntimeError):
    """No search index has been built yet."""


NO_INDEX_MESSAGE = (
    "⚠️ No search index is available yet.\n\n"
    "Please ask an administrator to build the index. This page uses it "
    "automatically as soon as it is ready."
)

LOW_RELEVANCE_NOTE = (
    "⚠️ Only loosely related papers were found in the database, so this answer may not "
    "address your question directly."
)

UNSAFE_ANSWER_MESSAGE = (
    "⛔ The answer was stopped because it may contain unsafe content.\n\n"
    "Please rephrase the question or ask about another research topic."
)

NO_RESULTS_MESSAGE = (
    "🔍 No sufficiently relevant papers were found in the database for this question.\n\n"
    "This system only answers from the indexed literature. Try other keywords, a more "
    "specific research question, or check that the topic is covered by the indexed data."
)

NOT_IN_PAPERS_MESSAGE = (
    "🔍 The papers found in the database do not answer this question.\n\n"
    "This system only states what the indexed papers say. Try a more specific question "
    "or other keywords."
)

LLM_DOWN_MESSAGE = (
    "⚠️ The language model is not available right now. Please try again in a minute; "
    "if this persists, ask an administrator to check the llm service."
)


# =====================================================
# Device helpers
# =====================================================

def resolve_device(requested: str) -> str:
    requested = (requested or "cpu").lower().strip()

    if requested.startswith("cuda"):
        if torch.cuda.is_available():
            return requested
        print("⚠️ CUDA requested but unavailable; falling back to CPU.")
        return "cpu"

    return requested


# =====================================================
# Index hot-reload
# =====================================================

def _refresh_if_index_changed():
    """Drop the BM25/vector handles when the embedding service published a new index."""
    global _retriever, _bm25_index, _bm25_corpus, _index_signature

    signature = index_signature()
    if signature != _index_signature:
        if _index_signature is not None:
            print("🔄 Search index changed on disk; reloading BM25 and vector retriever.")
        _retriever = None
        _bm25_index = None
        _bm25_corpus = None
        _index_signature = signature


def _collection_available(client) -> bool:
    if any(a.alias_name == QDRANT_COLLECTION for a in client.get_aliases().aliases):
        return True
    return client.collection_exists(QDRANT_COLLECTION)


# =====================================================
# BM25
# =====================================================

def load_bm25():
    if not os.path.exists(BM25_PATH):
        raise IndexNotReady(f"{BM25_PATH} not found")
    with open(BM25_PATH, "rb") as f:
        return pickle.load(f)


def get_bm25():
    global _bm25_index, _bm25_corpus

    with _load_lock:
        _refresh_if_index_changed()
        if _bm25_index is None:
            print("Loading BM25 index...")
            _bm25_index, _bm25_corpus = load_bm25()

        return _bm25_index, _bm25_corpus


# =====================================================
# Models / retriever
# =====================================================

def get_embed_model():
    global _embed_model

    with _load_lock:
        if _embed_model is None:
            embed_device = resolve_device(EMBED_DEVICE)
            print(f"Loading embedding model on {embed_device}...")
            _embed_model = HuggingFaceEmbedding(
                model_name=EMBED_MODEL_PATH,
                device=embed_device,
            )
            if embed_device.startswith("cuda") and SEARCH_FP16:
                _embed_model._model.half()
                torch.cuda.empty_cache()  # give the freed fp32 copy back (room for the safety models)
        return _embed_model


def chat(messages, max_tokens=64, grammar=None, temperature=0.0) -> str:
    """Short completion from the model server (question guard, follow-ups); no repeat penalty."""
    return llm.chat(messages, max_tokens=max_tokens, temperature=temperature, grammar=grammar,
                    repeat_penalty=1.0)[0]


def get_reranker():
    global _reranker

    with _load_lock:
        if _reranker is None:
            print("Loading reranker...")
            _reranker = build_reranker(RERANK_TOP_N)
        return _reranker


def get_retriever():
    """Vector retriever over the live Qdrant collection (alias QDRANT_COLLECTION)."""
    global _retriever

    with _load_lock:
        _refresh_if_index_changed()
        if _retriever is None:
            print("Connecting to Qdrant...")
            client = QdrantClient(
                url=QDRANT_URL,
                timeout=QDRANT_TIMEOUT,
            )
            if not _collection_available(client):
                raise IndexNotReady(f"Qdrant collection '{QDRANT_COLLECTION}' not found")

            vector_store = QdrantVectorStore(
                client=client,
                collection_name=QDRANT_COLLECTION,
            )
            # Nodes (text + metadata) live in the Qdrant payload, so no local
            # docstore is needed.
            index = VectorStoreIndex.from_vector_store(
                vector_store,
                embed_model=get_embed_model(),
            )
            _retriever = index.as_retriever(similarity_top_k=VECTOR_TOP_K)

        return _retriever


def models_loaded() -> bool:
    return _reranker is not None and _embed_model is not None and safety.loaded()


def load_models():
    """
    Load every model (at startup, or by the first question).  The safety
    models come last, after the model server has loaded, so their automatic
    GPU/CPU choice sees the real free GPU memory.
    """
    get_embed_model()
    get_reranker()
    if not llm.wait_until_ready(timeout=600):
        print(f"⚠️ Language model server not ready at {LLM_URL}.")
    safety.load()


def preload():
    """Background start-up: load the models and the index so the first question does not wait."""
    try:
        load_models()
        get_bm25()
        get_retriever()
        print("Models and index loaded.")
    except IndexNotReady as exc:
        print(f"Models loaded; no search index yet ({exc}).")
    except Exception as exc:
        print(f"⚠️ Preloading failed ({exc}); the first question will try again.")


def index_info() -> dict:
    """Description of the live index for the UI."""
    meta = read_index_meta()
    if meta:
        return {
            "ready": True,
            "papers": meta.get("papers"),
            "chunks": meta.get("chunks"),
            "built_at": meta.get("built_at"),
            "sources": [s.get("name") for s in meta.get("sources") or []],
        }
    return {"ready": os.path.exists(BM25_PATH), "legacy": os.path.exists(BM25_PATH)}


def build_reranker(top_n: int):
    """Create the reranker, preferring GPU but remaining version-compatible."""
    device = resolve_device(RERANK_DEVICE)

    try:
        reranker = SentenceTransformerRerank(
            model=RERANKER_PATH,
            top_n=top_n,
            device=device,
        )
    except TypeError:
        # Some llama-index versions do not expose `device` here.  In that
        # case sentence-transformers will select the available device itself.
        reranker = SentenceTransformerRerank(
            model=RERANKER_PATH,
            top_n=top_n,
        )
    if device.startswith("cuda") and SEARCH_FP16:
        reranker._model.model.half()
        torch.cuda.empty_cache()
    return reranker


def score_pairs(pairs):
    """Cross-encoder scores (0..1) of (text, passage) pairs; used to check answer sentences."""
    model = get_reranker()._model
    with _model_lock:
        return [float(s) for s in model.predict(pairs, show_progress_bar=False)]


# =====================================================
# Hybrid retrieval: Vector + BM25 + RRF + rerank
# =====================================================

def hybrid_query(question, top_k=None):
    if top_k is None:
        top_k = HYBRID_TOP_K

    retriever = get_retriever()
    reranker = get_reranker()

    # 1. Vector retrieval, reranked to RERANK_TOP_N.
    with _model_lock:
        vector_nodes = reranker.postprocess_nodes(
            retriever.retrieve(question),
            query_str=question,
        )

    # 2. BM25 retrieval
    bm25_index, bm25_docs = get_bm25()
    tokenized_query = question.lower().split()
    bm25_scores = bm25_index.get_scores(tokenized_query)
    bm25_top_idx = bm25_scores.argsort()[-30:][::-1]

    # 3. Reciprocal Rank Fusion
    rrf_k = 60
    fused = {}

    for rank, node in enumerate(vector_nodes):
        md = node.node.metadata or {}
        key = md.get("doi") or node.node.node_id

        if key not in fused:
            fused[key] = {
                "node": node.node,
                "score": 0.0,
            }

        fused[key]["score"] += 1.0 / (rrf_k + rank + 1)

    for rank, idx in enumerate(bm25_top_idx):
        doc = bm25_docs[idx]
        md = doc["metadata"]
        key = md.get("doi") or doc.get("node_id") or f"bm25_{idx}"

        if key not in fused:
            fused[key] = {
                "node": TextNode(
                    text=doc["text"],
                    metadata=md,
                    # Same as at ingest time: "source" is not part of the text.
                    excluded_embed_metadata_keys=["source"],
                    excluded_llm_metadata_keys=["source"],
                ),
                "score": 0.0,
            }

        fused[key]["score"] += 1.0 / (rrf_k + rank + 1)

    fused_list = sorted(
        fused.values(),
        key=lambda x: x["score"],
        reverse=True,
    )[:top_k]

    nodes = [
        NodeWithScore(node=item["node"], score=item["score"])
        for item in fused_list
    ]

    # 4. Cross-encoder rerank (cached model; returns at most RERANK_TOP_N,
    #    scores 0..1)
    with _model_lock:
        reranked_nodes = reranker.postprocess_nodes(
            nodes,
            query_str=question,
        )

    return reranked_nodes


# =====================================================
# Sources and references
# =====================================================

def _paper_key(metadata: dict) -> str:
    doi = (metadata.get("doi") or "").strip().lower()
    return doi or "title:" + (metadata.get("title") or "").strip().lower()


def _passage(text: str) -> str:
    """Passage text without the repeated title/keyword lines added for search."""
    seen, parts = set(), []
    for part in re.split(r"\n\s*\n", text.strip()):
        if part not in seen:
            seen.add(part)
            parts.append(part)
    return "\n".join(parts)


def build_sources(nodes):
    """
    The papers the answer is written from: numbered references (as shown to
    the user) and the passages given to the model and to the sentence check.
    """
    references, sources, number_of = [], [], {}
    for item in nodes:
        md = item.node.metadata or {}
        key = _paper_key(md)
        if key not in number_of:
            number_of[key] = len(references) + 1
            references.append({
                "n": number_of[key],
                "title": md.get("title") or "Unknown title",
                "doi": md.get("doi") or "",
                "year": md.get("year") or "",
                "journal": md.get("journal") or "",
                "source": md.get("source") or "",
                "score": round(float(item.score or 0), 4),
            })
        details = ", ".join(str(x) for x in (md.get("year"), md.get("journal")) if x)
        header = (md.get("title") or "Untitled") + (f" ({details})" if details else "")
        sources.append(grounding.Source(number_of[key], _passage(item.node.text), header))
    return references, sources


# =====================================================
# Grounded answer
# =====================================================

ANSWER_SYSTEM = (
    "You write answers for an offline literature search engine. You write ONLY from the "
    "numbered sources given with the question; they are abstracts of papers in the local "
    "database.\n"
    "Rules:\n"
    "- State only facts that the sources contain. Do not add background knowledge, examples, "
    "names, numbers or dates that are not in the sources.\n"
    "- End every sentence with the number of the source that supports it, like [2] or [1][3].\n"
    "- If the sources contain no information that answers the question, reply with exactly: "
    "NO_ANSWER\n"
    "- If they answer only part of the question, answer that part and say that the sources do "
    "not cover the rest.\n"
    "- Cover the relevant findings of every source that addresses the question, in two to four "
    "paragraphs (or a list), in the language of the question. Do not repeat yourself."
)

SUMMARY_SYSTEM = (
    "You summarize an answer of a literature search engine in two to four sentences. Use only "
    "statements from the answer, keep their source numbers like [2], and add nothing new."
)


def _answer_messages(question, sources):
    blocks = [f"[{s.n}] {s.header}\n{s.text}" for s in sources]
    return [
        {"role": "system", "content": ANSWER_SYSTEM},
        {"role": "user", "content": "Sources:\n\n" + "\n\n".join(blocks) + f"\n\nQuestion: {question}"},
    ]


class _NoAnswer(Exception):
    """The model said the sources do not answer the question."""


def generate_summary(answer_text, verifier):
    """Short summary of the (checked) answer; its sentences are checked as well."""
    try:
        text, _ = llm.chat(
            [{"role": "system", "content": SUMMARY_SYSTEM}, {"role": "user", "content": answer_text}],
            max_tokens=SUMMARY_MAX_TOKENS, temperature=LLM_TEMPERATURE)
        summary = verifier.render(text, final=True) if GROUNDING else text.strip()
        if summary:
            return summary
    except llm.LLMUnavailable as exc:
        print("Summary generation error:", exc)
    # Fallback: the first two sentences of the checked answer.
    return " ".join(grounding.split_sentences(re.sub(r"\s+", " ", answer_text))[:2])


def suggest_followups(question: str, summary: str):
    """Up to three follow-up research questions the user can click."""
    messages = [
        {
            "role": "system",
            "content": "You suggest follow-up questions for researchers using a scientific literature search engine.",
        },
        {
            "role": "user",
            "content": (
                f"Question: {question}\nAnswer summary: {summary}\n\n"
                "Suggest three short, specific follow-up research questions (at most 15 words "
                "each) that research papers could answer. One per line, no numbering."
            ),
        },
    ]
    try:
        text = chat(messages, max_tokens=120, temperature=0.2)
    except llm.LLMUnavailable as exc:
        print(f"Follow-up suggestions failed: {exc}")
        return []

    suggestions = []
    for line in text.splitlines():
        line = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", line).strip().strip('"')
        if len(line) < 10 or len(line) > 160 or find_hard_keyword(line):
            continue
        if not line.endswith("?"):
            line += "?"
        if line.lower() != question.lower() and line not in suggestions:
            suggestions.append(line)
    return suggestions[:3]


# =====================================================
# Main RAG response
# =====================================================

def answer_question(question: str, history=None, progress=None) -> dict:
    """
    Answer one question (optionally a follow-up in a conversation).

    progress(event, **data) receives "stage" events (stage, text) and "delta"
    events (text = the checked answer written so far).  Returns a dict with
    status = answered | blocked | no_results | no_index | unavailable | empty.
    """
    emit = progress or (lambda event, **data: None)
    question = (question or "").strip()
    if not question:
        return _final({"status": "empty", "message": "⚠️ Please enter a non-empty academic question."})

    if not _answer_slots.acquire(blocking=False):
        emit("stage", stage="queued", text="Waiting for a free answer slot…")
        _answer_slots.acquire()
    try:
        return _answer(question, history, emit)
    except llm.LLMUnavailable as exc:
        print(f"Language model unavailable: {exc}")
        return _final({"question": question, "status": "unavailable", "message": LLM_DOWN_MESSAGE})
    finally:
        _answer_slots.release()


def _answer(question, history, emit):
    started = time.time()

    def stage(name, text):
        emit("stage", stage=name, text=text)

    if not models_loaded():
        stage("loading", "Loading models (first question only)…")
        load_models()

    stage("check", "Checking the question…")
    standalone = standalone_question(question, history, chat)
    decision = check_question(question, chat=chat, standalone=standalone)
    print(f"Guard: {decision.category} ({decision.reason}): {question!r}"
          + (f" -> {standalone!r}" if standalone != question else ""))
    base = {
        "question": question,
        "standalone_question": standalone if standalone != question else None,
        "guard": {"category": decision.category, "reason": decision.reason},
    }
    if not decision.allowed:
        return _final({**base, "status": "blocked", "message": decision.message})

    papers = (read_index_meta() or {}).get("papers")
    stage("search", f"Searching {papers:,} papers…" if papers else "Searching papers…")
    try:
        nodes = hybrid_query(standalone, top_k=HYBRID_TOP_K)
    except IndexNotReady as exc:
        print(f"Index not ready: {exc}")
        return _final({**base, "status": "no_index", "message": NO_INDEX_MESSAGE})

    top_score = max((float(n.score or 0) for n in nodes), default=0.0)
    base["top_score"] = round(top_score, 4)
    if top_score < RELEVANCE_MIN_SCORE:
        print(f"No relevant papers (best score {top_score:.3f} < {RELEVANCE_MIN_SCORE})")
        return _final({**base, "status": "no_results", "message": NO_RESULTS_MESSAGE})
    if top_score < RELEVANCE_WARN_SCORE:
        base["notice"] = LOW_RELEVANCE_NOTE

    # The same papers are given to the model, checked against and listed.
    nodes = ([n for n in nodes if float(n.score or 0) >= SOURCE_MIN_SCORE] or nodes[:1])[:ANSWER_SOURCES]
    references, sources = build_sources(nodes)
    verifier = grounding.AnswerVerifier(sources, score_pairs, standalone)

    stage("answer", "Writing the answer…")
    # The safety model checks the text while it is written; of the checked
    # text only sentences supported by the sources are shown.
    monitor = safety.output_monitor(standalone)
    last_emit = [0.0]

    def on_text(raw):
        if raw.lstrip().startswith("NO_ANSWER"):
            raise _NoAnswer()
        text = raw
        if monitor:
            monitor.update(raw)  # raises safety.UnsafeOutput to stop the answer
            text = monitor.verified(raw)
        if GROUNDING:
            text = verifier.render(text)
        now = time.time()
        if now - last_emit[0] >= 0.15:
            last_emit[0] = now
            emit("delta", text=text)

    raw, finish, verdict = "", None, None
    try:
        raw, finish = llm.chat(_answer_messages(standalone, sources), max_tokens=LLM_ANSWER_TOKENS,
                               temperature=LLM_TEMPERATURE, on_text=on_text)
        verdict = monitor.finish(raw) if monitor else None
    except _NoAnswer:
        raw = "NO_ANSWER"
    except safety.UnsafeOutput as exc:
        verdict = exc.verdict
    finally:
        if monitor:
            monitor.close()
    if verdict and verdict.blocked:
        return _unsafe_answer(base, verdict)

    raw = raw.replace("NO_ANSWER", "").strip()
    # A reply cut off by the token limit ends with an unfinished sentence: drop it.
    answer = verifier.render(raw, final=finish != "length") if GROUNDING else raw
    base["grounding"] = {"checked": len(verifier.verdicts), "removed": len(verifier.removed)}
    for sentence, reason in verifier.removed:
        print(f"Grounding: dropped ({reason}): {sentence[:160]!r}")
    if not answer or (GROUNDING and not verifier.supported_claims()):
        return _final({**base, "status": "no_results", "message": NOT_IN_PAPERS_MESSAGE})
    emit("delta", text=answer)

    stage("summary", "Summarizing…")
    summary = generate_summary(answer, verifier)

    followups = []
    if SUGGEST_FOLLOWUPS:
        stage("followups", "Suggesting follow-up questions…")
        followups = suggest_followups(standalone, summary)

    verdict = safety.check_output(standalone, "\n".join([summary] + followups))
    if verdict and verdict.blocked:
        return _unsafe_answer(base, verdict)

    return _final({
        **base,
        "status": "answered",
        "summary": summary,
        "answer": answer,
        "references": references,
        "followups": followups,
        "seconds": round(time.time() - started, 1),
    })


def format_markdown(result: dict) -> str:
    """Markdown rendering of an answer (stored with the chat, used for export and copy)."""
    if result.get("status") != "answered":
        return result.get("message", "")

    md = ""
    if result.get("standalone_question"):
        md += f"*Searched for: {result['standalone_question']}*\n\n"
    if result.get("notice"):
        md += f"> {result['notice']}\n\n"
    md += "## ✅ Summary Answer\n\n" + result["summary"] + "\n\n"
    md += "---\n## 🧠 Detailed Answer (with citations)\n\n" + result["answer"] + "\n\n"

    if result.get("references"):
        md += "---\n## 📚 References\n\n"
        for ref in result["references"]:
            md += f"### [{ref['n']}] {ref['title']}\n"
            md += f"- **Relevance score:** `{ref['score']:.4f}`\n"
            md += f"- **Year:** {ref['year']}\n"
            md += f"- **Journal:** {ref['journal']}\n"
            if ref["doi"]:
                md += f"- **DOI:** https://doi.org/{ref['doi']}\n"
            md += "\n"

    if result.get("followups"):
        md += "---\n## 🔎 Related questions\n\n" + "".join(f"- {q}\n" for q in result["followups"])
    return md


def _unsafe_answer(base, verdict):
    print(f"Answer stopped by the safety check: {verdict.reason}")
    return _final({
        **base,
        "status": "blocked",
        "guard": {"category": verdict.guard_category, "reason": f"answer check: {verdict.reason}"},
        "message": UNSAFE_ANSWER_MESSAGE,
    })


def _final(result: dict) -> dict:
    result["markdown"] = format_markdown(result)
    return result
