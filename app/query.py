import os
import pickle
import re
import threading

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

from config import *
from filters import BANNED_KEYWORDS, contains_banned_content, is_academic_query, is_blocked_query
from index_meta import index_signature, read_index_meta


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


class IndexNotReady(RuntimeError):
    """No search index has been built yet."""


NO_INDEX_MESSAGE = (
    "⚠️ No search index is available yet.\n\n"
    "Open the **Embedding service** (port 7861), select the folder with the "
    "MySQL data and press **Start ingest**. This page uses the new index "
    "automatically when the ingest has finished."
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


def describe_index() -> str:
    """One-line Markdown status of the index for the UI header."""
    meta = read_index_meta()
    if meta:
        sources = meta.get("sources") or []
        names = ", ".join(s.get("name", "?") for s in sources[:3])
        if len(sources) > 3:
            names += f" +{len(sources) - 3} more"
        return (
            f"🗂️ Index: **{meta.get('papers', 0):,} papers** · "
            f"{meta.get('chunks', 0):,} chunks · built {meta.get('built_at', '?')}"
            + (f" · {names}" if names else "")
        )
    if os.path.exists(BM25_PATH):
        return "🗂️ Index: built by the previous ingest script."
    return "⚠️ No index yet. Use the Embedding service (port 7861) to ingest your MySQL data."


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

    # 1. Vector retrieval, reranked to RERANK_TOP_N.  (This used to run a full
    #    query engine, which also generated an LLM answer that was discarded.)
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
                "node": TextNode(text=doc["text"], metadata=md),
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

    # 4. Cross-encoder rerank (cached model; returns at most RERANK_TOP_N)
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


def complete_without_truncation(
    initial_prompt: str,
    max_continuations: int = None,
) -> str:
    """
    Generate a response in bounded chunks until the model emits END_MARKER.

    LLM_MAX_NEW_TOKENS therefore limits one generation call, not the final
    displayed answer.  If the model still fails to finish after the allowed
    continuation calls, an incomplete final prose fragment is removed.
    """
    if max_continuations is None:
        max_continuations = LLM_MAX_CONTINUATIONS

    llm = get_llm()

    first = str(llm.complete(initial_prompt)).strip()
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

        continuation = str(llm.complete(continuation_prompt)).strip()

        if not continuation:
            break

        combined = _merge_continuation(combined, continuation)

        if END_MARKER in combined:
            return _remove_end_marker(combined)

    # Safety fallback: never display a visibly half-written final sentence.
    return _trim_incomplete_tail(_remove_end_marker(combined))


# =====================================================
# Query validation
# =====================================================

def safe_query(question: str) -> str:
    question = question.strip()

    if not question:
        return "⚠️ Please enter a non-empty academic question."

    if is_blocked_query(question):
        return (
            "⛔ This system is restricted to academic literature research only.\n\n"
            "Political, religious, military, or ideological topics are not supported."
        )

    # Academic-intent filtering remains disabled, preserving your current
    # behavior.  Re-enable this block if desired.
    # if not is_academic_query(question):
    #     return (
    #         "⚠️ This system only supports academic research questions based "
    #         "on the paper database."
    #     )

    try:
        return query_with_sources(question)
    except IndexNotReady as exc:
        print(f"Index not ready: {exc}")
        return NO_INDEX_MESSAGE


# =====================================================
# Sentence helpers / citations
# =====================================================

def split_into_sentences(text: str):
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    return [s.strip() for s in sentences if len(s.strip()) > 10]


def keep_complete_sentences(text: str, max_sentences=None) -> str:
    """Return only complete prose sentences, optionally limiting count."""
    text = _remove_end_marker(text).strip()

    sentences = re.findall(
        r"[^.!?]+[.!?](?=\s|$)",
        text,
        flags=re.DOTALL,
    )
    sentences = [re.sub(r"\s+", " ", s).strip() for s in sentences if s.strip()]

    if max_sentences is not None:
        sentences = sentences[:max_sentences]

    return " ".join(sentences)


def embed_texts(embed_model, texts):
    return np.array([
        embed_model.get_text_embedding(t)
        for t in texts
    ])


def add_sentence_level_citations(answer_text: str, response):
    # Reuse the global embedding model.  Do not load a second copy on GPU.
    embed_model = get_embed_model()

    sentences = split_into_sentences(answer_text)

    if not sentences:
        return answer_text, []

    source_chunks = []
    source_meta = []

    for src in response.source_nodes:
        node = src.node
        source_chunks.append(node.text)
        source_meta.append(node.metadata or {})

    if not source_chunks:
        return answer_text, []

    sent_embeds = embed_texts(embed_model, sentences)
    chunk_embeds = embed_texts(embed_model, source_chunks)
    sim = cosine_similarity(sent_embeds, chunk_embeds)

    citation_map = {}
    references = []
    next_id = 1
    cited_sentences = []

    for i, sentence in enumerate(sentences):
        best_idx = int(np.argmax(sim[i]))
        best_score = float(sim[i][best_idx])
        md = source_meta[best_idx]

        title = md.get("title") or md.get("dc:title") or "Unknown title"
        doi = md.get("doi") or md.get("prism:doi") or ""
        year = md.get("year") or md.get("prism:coverDate") or ""
        journal = md.get("journal") or md.get("prism:publicationName") or ""

        key = (title, doi)

        if key not in citation_map:
            citation_map[key] = next_id
            references.append((next_id, title, doi, year, journal))
            next_id += 1

        cid = citation_map[key]

        if best_score > 0.3:
            cited_sentences.append(f"{sentence} [{cid}]")
        else:
            cited_sentences.append(sentence)

    return " ".join(cited_sentences), references


# =====================================================
# References
# =====================================================

def build_ranked_evidence_section(response):
    nodes = response.source_nodes

    if not nodes:
        return ""

    nodes = sorted(
        nodes,
        key=lambda x: float(x.score or 0),
        reverse=True,
    )

    md = "\n\n---\n## 📚 References\n\n"
    used = set()
    rank = 1

    for src in nodes:
        node = src.node
        meta = node.metadata or {}

        title = meta.get("title") or meta.get("dc:title") or "Unknown title"
        doi = meta.get("doi") or meta.get("prism:doi") or ""
        year = meta.get("year") or meta.get("prism:coverDate") or ""
        journal = meta.get("journal") or meta.get("prism:publicationName") or ""

        key = (title, doi)
        if key in used:
            continue
        used.add(key)

        score = float(src.score or 0)

        md += f"### [{rank}] {title}\n"
        md += f"- **Relevance score:** `{score:.4f}`\n"
        md += f"- **Year:** {year}\n"
        md += f"- **Journal:** {journal}\n"

        if doi:
            md += f"- **DOI:** https://doi.org/{doi}\n"

        md += "\n"
        rank += 1

    return md


# =====================================================
# Summary generation
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
    sents = split_into_sentences(full_answer)
    return " ".join(sents[:3])


# =====================================================
# Main RAG response
# =====================================================

def query_with_sources(question: str) -> str:
    reranked_nodes = hybrid_query(
        question,
        top_k=HYBRID_TOP_K,
    )

    if not reranked_nodes:
        return "⚠️ No relevant academic papers found."

    # Keep the original design: use the top reranked chunks as grounded context.
    context = "\n\n".join([
        n.node.text
        for n in reranked_nodes[:4]
    ])

    prompt = f"""
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
- When the entire answer is complete, append {END_MARKER}.

Question:
{question}

Sources:
{context}

Answer:
"""

    answer_text = complete_without_truncation(prompt)

    class FakeResponse:
        def __init__(self, nodes, response_text):
            self.source_nodes = nodes
            self.response = response_text

    response = FakeResponse(reranked_nodes, answer_text)

    if not response.source_nodes:
        return (
            "⚠️ No relevant academic papers were found in the database.\n"
            "This system does not answer questions without literature support."
        )

    summary = generate_summary(answer_text)
    cited_answer, references = add_sentence_level_citations(
        answer_text,
        response,
    )
    ranked_section = build_ranked_evidence_section(response)

    final = ""
    final += "## ✅ Summary Answer\n\n"
    final += summary + "\n\n"
    final += "---\n## 🧠 Detailed Answer (with citations)\n\n"
    final += cited_answer + "\n\n"
    final += ranked_section

    # Optional final safety filter preserved from your original code.
    # if contains_banned_content(final, BANNED_KEYWORDS):
    #     return (
    #         "⛔ The generated answer contains restricted content.\n\n"
    #         "Please reformulate your question."
    #     )

    return final
