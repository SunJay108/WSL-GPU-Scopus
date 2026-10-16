"""
The paper index in Qdrant: the embedding app writes it, the search app reads
it.  Both apps have a copy of this file; the copies must stay identical (the
search app refuses bundles whose INDEX_FORMAT differs from its own).

One point per chunk of a paper, with two vectors:
  dense   multilingual-e5-small embedding (meaning)
  bm25    sparse keyword vector: hashed words with BM25 term weights; Qdrant
          applies the IDF part itself (modifier "idf"), so the collection is
          a complete BM25 index that lives on disk.  Nothing has to be loaded
          into RAM, and the index grows to millions of papers.
Payload: text (the passage shown to the language model), title, doi, year,
journal, source, paper (one key per paper), chunk, batch (ingest run).

Collections built before this layout (one unnamed vector, llama-index
payload) are "legacy": the search app can still query them (dense only)
until a new index is imported.
"""

import json
import re
import uuid
import zlib
from collections import Counter

DENSE = "dense"
SPARSE = "bm25"

# Identifies this layout: vector names, keyword tokenizer and hashing, BM25
# weights and the e5 "passage: "/"query: " prefixes.  The search app must use
# the same version of this file; it checks this value when importing a bundle.
INDEX_FORMAT = "scopus-papers-v2"

# BM25 term-frequency saturation and length normalisation.
K1 = 1.2
B = 0.75
DEFAULT_AVG_TOKENS = 220

_WORD = re.compile(r"[^\W_]+", re.UNICODE)
_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿]")   # Chinese/Japanese: no spaces
STOPWORDS = frozenset("""
a an and are as at be been being but by can could did do does for from had has have how if in into is
it its may might more most no not of on or our over such than that the their them then there these
they this those through to under was we were what when where which while who whom why will with would
""".split())


# =====================================================
# Keyword (sparse) vectors
# =====================================================

def tokenize(text):
    """Lower-case words without stopwords; Chinese/Japanese text as character pairs."""
    tokens = []
    for word in _WORD.findall(text.lower()):
        if _CJK.search(word):
            tokens.extend(word[i:i + 2] for i in range(max(1, len(word) - 1)))
        elif len(word) > 1 and word not in STOPWORDS:
            tokens.append(word)
    return tokens


def _term_counts(tokens):
    counts = Counter()
    for token in tokens:
        counts[zlib.crc32(token.encode("utf-8"))] += 1
    return counts


def sparse_document(text, avg_tokens=DEFAULT_AVG_TOKENS):
    """(indices, values) with BM25 term weights tf*(k1+1) / (tf + k1*(1-b+b*len/avg))."""
    tokens = tokenize(text)
    norm = K1 * (1 - B + B * len(tokens) / max(avg_tokens, 1))
    counts = _term_counts(tokens)
    indices = list(counts)
    return indices, [tf * (K1 + 1) / (tf + norm) for tf in counts.values()]


def sparse_query(text):
    """(indices, values) of a question; Qdrant multiplies each term by its IDF."""
    counts = _term_counts(tokenize(text))
    return list(counts), [1.0] * len(counts)


# =====================================================
# Papers -> chunks
# =====================================================

def paper_key(doi, title):
    doi = (doi or "").strip().lower()
    return doi or "title:" + re.sub(r"\s+", " ", (title or "").strip().lower())


def header(paper):
    lines = []
    if paper.get("title"):
        lines.append("Title: " + paper["title"])
    if paper.get("keywords"):
        lines.append("Keywords: " + ", ".join(paper["keywords"]))
    if paper.get("authors"):
        lines.append("Authors: " + ", ".join(paper["authors"]))
    if paper.get("journal"):
        lines.append("Journal: " + paper["journal"])
    if paper.get("year"):
        lines.append("Year: " + str(paper["year"]))
    return "\n".join(lines)


def _sentences(text):
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]


def chunk_paper(paper, max_chars, overlap_chars):
    """
    Passages of one paper: the header (title, keywords, authors, journal,
    year) and the abstract; a long abstract is split at sentence boundaries
    with some overlap, and every part keeps the header.
    """
    head = header(paper)
    abstract = paper.get("abstract") or ""
    budget = max(400, max_chars - len(head))
    if len(abstract) <= budget:
        return [f"{head}\nAbstract:\n{abstract}"]

    parts, current = [], []
    for sentence in _sentences(abstract):
        if current and len(" ".join(current + [sentence])) > budget:
            parts.append(" ".join(current))
            # Start the next part with the last sentences (overlap).
            tail, size = [], 0
            for s in reversed(current):
                if size + len(s) > overlap_chars:
                    break
                tail.insert(0, s)
                size += len(s) + 1
            current = tail
        current.append(sentence)
    if current:
        parts.append(" ".join(current))
    return [f"{head}\nAbstract:\n{part}" for part in parts]


def keyword_text(paper, chunk):
    """Text the keyword vector is built from: the title and keywords count twice."""
    boost = " ".join(x for x in (paper.get("title"), ", ".join(paper.get("keywords") or [])) if x)
    return f"{boost}\n{chunk}"


def point_id(key, chunk_no):
    """Stable id: indexing the same paper again overwrites its points instead of duplicating them."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{key}#{chunk_no}"))


# =====================================================
# Collections
# =====================================================

def create_collection(client, name, dim):
    from qdrant_client.http import models

    client.create_collection(
        collection_name=name,
        vectors_config={DENSE: models.VectorParams(size=dim, distance=models.Distance.COSINE)},
        # Every query reads the vectors, the graph and the keyword index, so
        # they are kept in RAM (about 2.2 GB per million chunks); on a hard
        # disk each cold read would cost milliseconds.
        sparse_vectors_config={SPARSE: models.SparseVectorParams(
            index=models.SparseIndexParams(on_disk=False), modifier=models.Modifier.IDF)},
        hnsw_config=models.HnswConfigDiff(m=16, ef_construct=128),
        # The texts (the largest part) stay on disk: only the few papers of
        # an answer are read per question.
        on_disk_payload=True,
    )
    for field in ("paper", "batch"):
        client.create_payload_index(name, field_name=field, field_schema=models.PayloadSchemaType.KEYWORD)


def is_legacy(client, collection):
    """True for collections built before the dense + bm25 layout."""
    params = client.get_collection(collection).config.params
    return not (isinstance(params.vectors, dict) and DENSE in params.vectors
                and params.sparse_vectors and SPARSE in params.sparse_vectors)


def dense_size(client, collection):
    vectors = client.get_collection(collection).config.params.vectors
    return vectors[DENSE].size if isinstance(vectors, dict) and DENSE in vectors else vectors.size


def payload_text_and_metadata(payload):
    """(text, metadata) of a point in either layout."""
    if "_node_content" in payload:                    # legacy llama-index payload
        node = json.loads(payload["_node_content"])
        return node.get("text", ""), {k: payload.get(k, "") for k in ("title", "doi", "year", "journal", "source")}
    return payload.get("text", ""), {k: payload.get(k, "") for k in ("title", "doi", "year", "journal", "source")}
