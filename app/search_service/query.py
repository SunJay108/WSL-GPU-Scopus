"""
Answering pipeline: hybrid retrieval (Qdrant vectors + BM25, RRF fusion,
cross-encoder rerank), grounded generation with the local LLM, summary,
citations and follow-up suggestions.  Reloads the index when the embedding
service publishes a new one.
"""

import os
import pickle
import re
import threading
import time

import numpy as np
# torch must be imported before llama_cpp (via LlamaCPP below): llama.cpp links
# the system NCCL, and loading it first breaks torch ("undefined symbol: ncclCommResume").
import torch
from sklearn.metrics.pairwise import cosine_similarity

from llama_index.core import VectorStoreIndex
from llama_index.core.postprocessor import SentenceTransformerRerank
from llama_index.core.schema import NodeWithScore, TextNode
from llama_index.embeddings.huggingface import HuggingFaceEmbedding
from llama_index.llms.llama_cpp import LlamaCPP
from llama_index.vector_stores.qdrant import QdrantVectorStore
from qdrant_client import QdrantClient

from core.config import *
from .filters import find_hard_keyword
from .followup import standalone_question
from .guard import check_question
from core.index_meta import index_signature, read_index_meta


# =====================================================
# Global singletons
# =====================================================

_llm = None
_embed_model = None
_reranker = None

# Rebuilt whenever the embedding service publishes a new index.
_retriever = None
_bm25_index = None
_bm25_corpus = None
_index_signature = None

_load_lock = threading.RLock()
# One llama.cpp model instance: answer one question at a time.
_answer_lock = threading.Lock()


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

NO_RESULTS_MESSAGE = (
    "🔍 No sufficiently relevant papers were found in the database for this question.\n\n"
    "This system only answers from the indexed literature. Try other keywords, a more "
    "specific research question, or check that the topic is covered by the indexed data."
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
        return _embed_model


def get_llm():
    global _llm

    with _load_lock:
        if _llm is None:
            print("Loading LLM...")
            print(
                "LLM settings:",
                f"ctx={LLM_CTX},",
                f"batch={LLM_BATCH},",
                f"new_tokens/call={LLM_MAX_NEW_TOKENS},",
                f"gpu_layers={LLM_GPU_LAYERS}",
            )

            _llm = LlamaCPP(
                model_path=LLM_PATH,
                temperature=LLM_TEMPERATURE,
                max_new_tokens=LLM_MAX_NEW_TOKENS,
                context_window=LLM_CTX,
                model_kwargs={
                    "n_ctx": LLM_CTX,
                    "n_batch": LLM_BATCH,
                    "n_threads": LLM_THREADS,
                    "n_gpu_layers": LLM_GPU_LAYERS,
                    "repeat_penalty": LLM_REPEAT_PENALTY,
                    "top_p": LLM_TOP_P,
                    "top_k": LLM_TOP_K,
                },
                verbose=False,
            )
        return _llm


def chat(messages, max_tokens=64, grammar=None, temperature=0.0) -> str:
    """Short chat-formatted completion (uses the model's own chat template)."""
    model = get_llm()._model
    out = model.create_chat_completion(
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
        grammar=grammar,
    )
    return (out["choices"][0]["message"].get("content") or "").strip()


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
    return _llm is not None and _reranker is not None and _embed_model is not None


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
        return SentenceTransformerRerank(
            model=RERANKER_PATH,
            top_n=top_n,
            device=device,
        )
    except TypeError:
        # Some llama-index versions do not expose `device` here.  In that
        # case sentence-transformers will select the available device itself.
        return SentenceTransformerRerank(
            model=RERANKER_PATH,
            top_n=top_n,
        )


# =====================================================
# Hybrid retrieval: Vector + BM25 + RRF + rerank
# =====================================================

def hybrid_query(question, top_k=None):
    if top_k is None:
        top_k = HYBRID_TOP_K

    retriever = get_retriever()
    reranker = get_reranker()

    # 1. Vector retrieval, reranked to RERANK_TOP_N.
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
    reranked_nodes = reranker.postprocess_nodes(
        nodes,
        query_str=question,
    )

    return reranked_nodes


# =====================================================
# Robust anti-truncation generation
# =====================================================

def _strip_model_prefixes(text: str) -> str:
    text = text.strip()
    text = re.sub(
        r"^(answer|response|continuation|concise summary|summary)\s*:\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    return text.strip()


def _remove_end_marker(text: str) -> str:
    return text.replace(END_MARKER, "").strip()


def _visible(text: str) -> str:
    """Text safe to show while streaming: no END_MARKER, not even half of one."""
    text = _strip_model_prefixes(text.replace(END_MARKER, ""))
    for size in range(len(END_MARKER) - 1, 0, -1):
        if text.endswith(END_MARKER[:size]):
            return text[:-size]
    return text


def _trim_incomplete_tail(text: str) -> str:
    """Remove only an obviously unfinished final prose fragment."""
    text = text.strip()

    if not text:
        return text

    # Already looks complete.
    if text.endswith((".", "!", "?", "```", ")", "]")):
        return text

    # Keep through the final sentence terminator.  This is a last-resort
    # fallback used only when the model never returned END_MARKER.
    last_positions = [text.rfind("."), text.rfind("!"), text.rfind("?")]
    last = max(last_positions)

    if last >= 0:
        return text[: last + 1].strip()

    return text


def _merge_continuation(existing: str, continuation: str) -> str:
    """Join continuation while removing a small repeated overlap if present."""
    existing = existing.rstrip()
    continuation = _strip_model_prefixes(continuation).lstrip()

    if not existing:
        return continuation
    if not continuation:
        return existing

    # Character overlap guard for local models that repeat the end of the
    # previous chunk.  Search a bounded overlap from longest to shortest.
    max_overlap = min(500, len(existing), len(continuation))
    for size in range(max_overlap, 39, -1):
        if existing[-size:].strip() == continuation[:size].strip():
            continuation = continuation[size:].lstrip()
            break

    if not continuation:
        return existing

    return existing + "\n" + continuation


def _generate(prompt: str, on_partial=None) -> str:
    """One bounded completion; streams the growing text to on_partial if given."""
    llm = get_llm()
    if on_partial is None:
        return str(llm.complete(prompt)).strip()

    text = ""
    for part in llm.stream_complete(prompt):
        text = part.text
        on_partial(text)
    return text.strip()


def complete_without_truncation(
    initial_prompt: str,
    max_continuations: int = None,
    on_text=None,
) -> str:
    """
    Generate a response in bounded chunks until the model emits END_MARKER.

    LLM_MAX_NEW_TOKENS therefore limits one generation call, not the final
    displayed answer.  If the model still fails to finish after the allowed
    continuation calls, an incomplete final prose fragment is removed.
    on_text(visible_text) receives the answer while it is being generated.
    """
    if max_continuations is None:
        max_continuations = LLM_MAX_CONTINUATIONS

    def stream(prefix):
        if on_text is None:
            return None
        return lambda partial: on_text(_visible((prefix + "\n" + partial) if prefix else partial))

    first = _generate(initial_prompt, stream(""))
    combined = _strip_model_prefixes(first)

    if END_MARKER in combined:
        return _remove_end_marker(combined)

    for _ in range(max_continuations):
        tail = combined[-CONTINUATION_TAIL_CHARS:]

        continuation_prompt = f"""
You are continuing an academic answer that may have been cut off by a generation limit.

Rules:
- If the answer below is already complete, output only {END_MARKER}.
- Otherwise continue exactly from where it stopped.
- Do not restart the answer.
- Do not repeat earlier sentences or conclusions.
- Finish the interrupted sentence first, then continue only as needed.
- When the whole answer is complete, append {END_MARKER}.

Recent end of the existing answer:
{tail}

Continuation:
"""

        continuation = _generate(continuation_prompt, stream(combined))

        if not continuation:
            break

        combined = _merge_continuation(combined, continuation)

        if END_MARKER in combined:
            return _remove_end_marker(combined)

    # Safety fallback: never display a visibly half-written final sentence.
    return _trim_incomplete_tail(_remove_end_marker(combined))


# =====================================================
# Sentences and citations
# =====================================================

# Periods that do not end a sentence ("Smith et al. (2024) showed ...").
_ABBREVIATIONS = (
    "et al.", "e.g.", "i.e.", "etc.", "vs.", "cf.", "approx.",
    "Fig.", "Figs.", "Eq.", "Ref.", "No.", "Vol.", "Dr.", "Prof.",
)
_DOT = "․"  # placeholder for protected periods


def split_into_sentences(text: str, min_length: int = 0):
    protected = text.strip()
    for abbr in _ABBREVIATIONS:
        protected = protected.replace(abbr, abbr.replace(".", _DOT))
    protected = re.sub(r"\b([A-Z])\.(?=\s+[A-Z])", r"\1" + _DOT, protected)  # initials

    sentences = re.split(r"(?<=[.!?])\s+", protected)
    sentences = [s.replace(_DOT, ".").strip() for s in sentences]
    return [s for s in sentences if len(s) > min_length]


def keep_complete_sentences(text: str, max_sentences=None) -> str:
    """Return only complete prose sentences, optionally limiting count."""
    text = re.sub(r"\s+", " ", _remove_end_marker(text)).strip()
    sentences = [s for s in split_into_sentences(text) if s.endswith((".", "!", "?"))]

    if max_sentences is not None:
        sentences = sentences[:max_sentences]

    return " ".join(sentences)


def _paper_key(metadata: dict) -> str:
    doi = (metadata.get("doi") or "").strip().lower()
    return doi or "title:" + (metadata.get("title") or "").strip().lower()


def build_references(nodes):
    """Distinct papers of the source chunks, numbered by relevance rank."""
    references = []
    seen = set()
    for item in nodes:
        md = item.node.metadata or {}
        key = _paper_key(md)
        if key in seen:
            continue
        seen.add(key)
        references.append({
            "n": len(references) + 1,
            "title": md.get("title") or "Unknown title",
            "doi": md.get("doi") or "",
            "year": md.get("year") or "",
            "journal": md.get("journal") or "",
            "source": md.get("source") or "",
            "score": round(float(item.score or 0), 4),
        })
    return references


def add_sentence_level_citations(answer_text: str, nodes, references) -> str:
    """
    Append [n] to sentences that are clearly supported by a source chunk;
    n is the number of that paper in `references`.  Paragraphs and list items
    of the answer are preserved.
    """
    if not nodes or not references:
        return answer_text

    number_of = {_paper_key(ref): ref["n"] for ref in references}

    # Split into blocks/lines/sentences, remembering the layout.
    blocks = []
    for block in re.split(r"\n\s*\n", answer_text.strip()):
        lines = [ln for ln in block.split("\n") if ln.strip()]
        is_list = any(re.match(r"^\s*(?:[-*•]|\d+[.)])\s+", ln) for ln in lines)
        units = lines if is_list else [" ".join(ln.strip() for ln in lines)]
        blocks.append((is_list, [split_into_sentences(u) for u in units]))

    sentences = [s for _, units in blocks for unit in units for s in unit]
    # Only real sentences get a citation, not headings like "Main methods include:".
    candidates = [s for s in sentences if len(s) > 10 and s.rstrip("*_ ").endswith((".", "!", "?"))]
    if not candidates:
        return answer_text

    embed_model = get_embed_model()
    sent_embeds = np.array(embed_model.get_text_embedding_batch(candidates))
    chunk_embeds = np.array(embed_model.get_text_embedding_batch([n.node.text for n in nodes]))
    sim = cosine_similarity(sent_embeds, chunk_embeds)

    cite_for = {}
    for i, sentence in enumerate(candidates):
        best = int(np.argmax(sim[i]))
        if float(sim[i][best]) > 0.3:
            n = number_of.get(_paper_key(nodes[best].node.metadata or {}))
            if n:
                cite_for[sentence] = n

    def cite(sentence):
        n = cite_for.get(sentence)
        if not n:
            return sentence
        # Put the marker before the final punctuation: "... results [2]."
        match = re.match(r"^(.*?)([.!?]+)$", sentence)
        return f"{match.group(1)} [{n}]{match.group(2)}" if match else f"{sentence} [{n}]"

    out_blocks = []
    for is_list, units in blocks:
        rendered = [" ".join(cite(s) for s in unit) for unit in units]
        out_blocks.append("\n".join(rendered) if is_list else rendered[0])
    return "\n\n".join(out_blocks)


# =====================================================
# Summary and follow-up suggestions
# =====================================================

def generate_summary(full_answer: str) -> str:
    prompt = f"""
You are an academic editor.

Create a short summary of the answer below.

STRICT RULES:
- Write 3 to {SUMMARY_MAX_SENTENCES} complete sentences.
- Use no more than about {SUMMARY_MAX_WORDS} words.
- Put the most important conclusion first.
- Keep only the most important findings.
- Do not repeat an idea, phrase, or conclusion.
- Do not use bullet points.
- Do not add references or citations.
- Never stop in the middle of a sentence.
- After the final complete sentence, append {END_MARKER}.

Answer to summarize:
{full_answer}

Concise summary:
"""

    try:
        result = complete_without_truncation(
            prompt,
            max_continuations=SUMMARY_MAX_CONTINUATIONS,
        )

        clean = keep_complete_sentences(
            result,
            max_sentences=SUMMARY_MAX_SENTENCES,
        )

        if clean:
            return clean

    except Exception as exc:
        print("Summary generation error:", exc)

    # Deterministic fallback: only complete sentences.
    sents = split_into_sentences(full_answer, min_length=10)
    return " ".join(sents[:3])


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
    except Exception as exc:
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

ANSWER_PROMPT = """
You are an academic research assistant.

Answer the following question using ONLY the provided sources.

Rules:
- Be factual and academically precise.
- Do not invent information that is absent from the sources.
- Do not repeat the same idea or conclusion.
- Use coherent paragraphs and complete sentences.
- Be concise but sufficiently detailed to answer the question.
- If the available sources do not support a requested point, say so.
- Never stop in the middle of a sentence.
- When the entire answer is complete, append {end_marker}.

Question:
{question}

Sources:
{context}

Answer:
"""


def answer_question(question: str, history=None, progress=None) -> dict:
    """
    Answer one question (optionally a follow-up in a conversation).

    progress(event, **data) receives "stage" events (stage, text) and "delta"
    events (text = the answer written so far).  Returns a dict with
    status = answered | blocked | no_results | no_index | empty.
    """
    emit = progress or (lambda event, **data: None)
    question = (question or "").strip()
    if not question:
        return _final({"status": "empty", "message": "⚠️ Please enter a non-empty academic question."})

    if not _answer_lock.acquire(blocking=False):
        emit("stage", stage="queued", text="Waiting for the previous question to finish…")
        _answer_lock.acquire()
    try:
        return _answer(question, history, emit)
    finally:
        _answer_lock.release()


def _answer(question, history, emit):
    timings = {}
    started = time.time()

    def stage(name, text):
        timings[name] = time.time()
        emit("stage", stage=name, text=text)

    if not models_loaded():
        stage("loading", "Loading models (first question only)…")
        get_llm(), get_embed_model(), get_reranker()

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

    nodes = [n for n in nodes if float(n.score or 0) >= SOURCE_MIN_SCORE] or nodes[:1]
    context = "\n\n".join(n.node.text for n in nodes[:4])

    stage("answer", "Writing the answer…")
    last_emit = [0.0]

    def on_text(text):
        now = time.time()
        if now - last_emit[0] >= 0.15:
            last_emit[0] = now
            emit("delta", text=text)

    answer_text = complete_without_truncation(
        ANSWER_PROMPT.format(end_marker=END_MARKER, question=standalone, context=context),
        on_text=on_text,
    )
    emit("delta", text=answer_text)

    stage("summary", "Summarizing…")
    summary = generate_summary(answer_text)

    stage("cite", "Adding citations…")
    references = build_references(nodes)
    cited_answer = add_sentence_level_citations(answer_text, nodes, references)

    followups = []
    if SUGGEST_FOLLOWUPS:
        stage("followups", "Suggesting follow-up questions…")
        followups = suggest_followups(standalone, summary)

    return _final({
        **base,
        "status": "answered",
        "summary": summary,
        "answer": cited_answer,
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


def _final(result: dict) -> dict:
    result["markdown"] = format_markdown(result)
    return result
