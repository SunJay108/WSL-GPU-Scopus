"""
Answering pipeline: hybrid retrieval in Qdrant (vector + BM25 keyword search,
reciprocal rank fusion), cross-encoder rerank, grounded generation by the
language model server (llm.py), summary, citations and follow-up
suggestions.  The index is searched through its Qdrant alias, so a newly
published index is used at once and nothing large is held in memory.

Answers contain only what the retrieved papers say: the model writes from
numbered sources, and every sentence is checked against them before it is
shown (grounding.py); unsupported sentences are dropped.  Up to LLM_PARALLEL
questions are answered at the same time.
"""

import re
import threading
import time

import torch

from llama_index.core.postprocessor import SentenceTransformerRerank
from llama_index.core.schema import NodeWithScore, TextNode
from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from core import embedding, paper_index
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
_qdrant = None

# Layout of the live collection, re-checked when a new index is published.
_index_signature = None
_legacy_index = None

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
# Index (Qdrant)
# =====================================================

def get_qdrant():
    global _qdrant
    with _load_lock:
        if _qdrant is None:
            _qdrant = QdrantClient(url=QDRANT_URL, timeout=QDRANT_TIMEOUT)
        return _qdrant


def _index_is_legacy():
    """
    True when the live collection has the layout of earlier versions (dense
    vectors only, keyword index in bm25.pkl); re-checked after each publish.
    """
    global _index_signature, _legacy_index
    with _load_lock:
        signature = index_signature()
        if _legacy_index is None or signature != _index_signature:
            client = get_qdrant()
            if not (any(a.alias_name == QDRANT_COLLECTION for a in client.get_aliases().aliases)
                    or client.collection_exists(QDRANT_COLLECTION)):
                raise IndexNotReady(f"Qdrant collection '{QDRANT_COLLECTION}' not found")
            published = _legacy_index is not None      # a new index replaced the one in use
            _legacy_index = paper_index.is_legacy(client, QDRANT_COLLECTION)
            _index_signature = signature
            if _legacy_index:
                print("⚠️ The index has the layout of an earlier version: vector search only (no keyword "
                      "search) until the next 'Replace index' ingest.")
            if published:
                threading.Thread(target=warm_up_index, name="index-warm-up", daemon=True).start()
        return _legacy_index


# =====================================================
# Models
# =====================================================

def get_embed_model():
    global _embed_model

    with _load_lock:
        if _embed_model is None:
            print(f"Loading embedding model on {resolve_device(EMBED_DEVICE)}...")
            _embed_model = embedding.load(EMBED_DEVICE, fp16=SEARCH_FP16)
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
        hybrid_query("warm-up")
        warm_up_index()
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
            "legacy": meta.get("layout") != "dense+bm25",
        }
    return {"ready": False}


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
    """
    Best papers for `question`: vector search and keyword (BM25) search run
    in Qdrant and are fused by reciprocal rank; the top_k distinct papers
    are reranked by the cross-encoder (scores 0..1, at most RERANK_TOP_N).
    """
    nodes = _candidates(question, top_k or HYBRID_TOP_K)
    with _model_lock:
        return get_reranker().postprocess_nodes(nodes, query_str=question)


# Varied questions that page the index into memory after a start or a new
# index; the first searches on a cold index read from disk and take seconds.
WARM_UP_QUESTIONS = [
    "deep learning methods for image classification", "climate change effects on agriculture",
    "lithium-ion battery degradation", "public health interventions for obesity",
    "supply chain management and resilience", "corrosion of steel in acidic solutions",
    "remote work and employee productivity", "cancer immunotherapy clinical outcomes",
    "graphene composites for energy storage", "consumer behaviour in online retail",
    "fault detection in electric motors", "water treatment by membrane filtration",
    "language learning in higher education", "concrete compressive strength prediction",
    "gut microbiota and metabolic disease", "renewable energy policy and economics",
]


def warm_up_index():
    started = time.time()
    try:
        for question in WARM_UP_QUESTIONS:
            _candidates(question, HYBRID_TOP_K)
        print(f"Search index warmed up in {time.time() - started:.0f}s.")
    except Exception as exc:
        print(f"⚠️ Index warm-up stopped: {exc}")


def _candidates(question, top_k):
    """The top_k distinct papers of the fused vector + keyword search (before reranking)."""
    client = get_qdrant()
    legacy = _index_is_legacy()

    with _model_lock:
        dense = embedding.embed_query(get_embed_model(), question, legacy=legacy)
    # Several chunks of one paper may match: ask for a few more and keep one
    # per paper (each extra point is one more text read from disk).
    limit = top_k + top_k // 2
    if legacy:
        points = client.query_points(QDRANT_COLLECTION, query=dense, limit=limit, with_payload=True).points
    else:
        prefetch = [qmodels.Prefetch(query=dense, using=paper_index.DENSE, limit=VECTOR_TOP_K)]
        indices, values = paper_index.sparse_query(question)
        if indices:
            prefetch.append(qmodels.Prefetch(query=qmodels.SparseVector(indices=indices, values=values),
                                             using=paper_index.SPARSE, limit=BM25_TOP_K))
        points = client.query_points(QDRANT_COLLECTION, prefetch=prefetch,
                                     query=qmodels.FusionQuery(fusion=qmodels.Fusion.RRF),
                                     limit=limit, with_payload=True).points

    nodes, papers = [], set()
    for point in points:
        text, metadata = paper_index.payload_text_and_metadata(point.payload or {})
        key = paper_index.paper_key(metadata.get("doi"), metadata.get("title"))
        if key in papers:
            continue
        papers.add(key)
        nodes.append(NodeWithScore(node=TextNode(text=text, metadata=metadata), score=point.score))
        if len(nodes) >= top_k:
            break
    return nodes


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
