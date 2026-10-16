"""
Settings for both services, read from environment variables (docker-compose.yml
passes the values from .env).  Numbers are clamped to safe ranges so a typo
cannot break the application.
"""

import os


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except (TypeError, ValueError):
        print(f"⚠️ Invalid {name}={raw!r}; using {default}")
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw.strip())
    except (TypeError, ValueError):
        print(f"⚠️ Invalid {name}={raw!r}; using {default}")
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _clamp(value, minimum, maximum):
    return max(minimum, min(value, maximum))


# =====================================================
# Shared: models, data, search index, accounts
# =====================================================

EMBED_MODEL_PATH = os.getenv("EMBED_MODEL_PATH", "/app/models/multilingual-e5-small")
RERANKER_PATH = os.getenv("RERANKER_PATH", "/app/models/bge-reranker-base")

# "cuda" is preferred; both services fall back to the CPU when CUDA is unavailable.
EMBED_DEVICE = os.getenv("EMBED_DEVICE", "cuda").strip().lower()
RERANK_DEVICE = os.getenv("RERANK_DEVICE", "cuda").strip().lower()

BASE_DATA_DIR = os.getenv("BASE_DATA_DIR", "/app/app/data")
STORAGE_PATH = os.getenv("STORAGE_PATH", f"{BASE_DATA_DIR}/storage")

QDRANT_URL = os.getenv("QDRANT_URL", "http://qdrant:6333")
QDRANT_TIMEOUT = _clamp(_env_int("QDRANT_TIMEOUT", 300), 30, 1800)

# Written by the embedding service, read by the search UI.  QDRANT_COLLECTION
# is a Qdrant alias that always points at the live collection, so a rebuilt
# index is swapped in atomically.  The collection holds the dense vectors and
# the keyword (BM25) index (core/paper_index.py).
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "papers")
INDEX_META_PATH = os.getenv("INDEX_META_PATH", f"{STORAGE_PATH}/index_meta.json")
# Keyword index file of the earlier layout; removed by the next Replace ingest.
BM25_PATH = os.getenv("BM25_PATH", f"{STORAGE_PATH}/bm25.pkl")

# Accounts, chat history and the activity log (one SQLite file).
HISTORY_DB_PATH = os.getenv("HISTORY_DB_PATH", f"{BASE_DATA_DIR}/history/chat_history.sqlite3")

# Anyone who can open a page may create an account when ALLOW_SIGNUP is true
# (the first account always can, and becomes admin); otherwise only
# administrators add users.  Only administrators can use the embedding service.
ALLOW_SIGNUP = _env_bool("ALLOW_SIGNUP", True)
SESSION_DAYS = _clamp(_env_int("SESSION_DAYS", 7), 1, 365)


# =====================================================
# Search service: language model
# =====================================================

SEARCH_SERVICE_PORT = _clamp(_env_int("SEARCH_SERVICE_PORT", 7860), 1, 65535)
# Load all models when the service starts instead of at the first question.
PRELOAD_MODELS = _env_bool("PRELOAD_MODELS", True)

# The model runs in its own service (llama.cpp llama-server, compose service
# "llm"); its model file, context and GPU settings are set there.
LLM_URL = os.getenv("LLM_URL", "http://llm:8080")
LLM_TIMEOUT = _clamp(_env_int("LLM_TIMEOUT", 300), 10, 3600)
# Questions answered at the same time.  Must equal the server's parallel slots
# (docker-compose.yml passes the same LLM_PARALLEL to both).
LLM_PARALLEL = _clamp(_env_int("LLM_PARALLEL", 1), 1, 64)

# Greedy decoding (temperature 0) keeps answers close to the sources.
LLM_TEMPERATURE = _clamp(_env_float("LLM_TEMPERATURE", 0.0), 0.0, 1.5)
LLM_REPEAT_PENALTY = _clamp(_env_float("LLM_REPEAT_PENALTY", 1.1), 1.0, 1.5)
LLM_TOP_P = _clamp(_env_float("LLM_TOP_P", 0.90), 0.05, 1.0)
LLM_TOP_K = _clamp(_env_int("LLM_TOP_K", 40), 1, 200)

# Longest answer (tokens, about 4 characters each) and summary.
LLM_ANSWER_TOKENS = _clamp(_env_int("LLM_ANSWER_TOKENS", 900), 200, 4096)
SUMMARY_MAX_TOKENS = _clamp(_env_int("SUMMARY_MAX_TOKENS", 220), 60, 600)


# =====================================================
# Search service: retrieval, guard, chat
# =====================================================

# Candidates from the vector search and from the keyword (BM25) search, fused
# by reciprocal rank in Qdrant; the best HYBRID_TOP_K papers go to the
# cross-encoder, which keeps RERANK_TOP_N.
VECTOR_TOP_K = _clamp(_env_int("VECTOR_TOP_K", 30), 4, 100)
BM25_TOP_K = _clamp(_env_int("BM25_TOP_K", 30), 4, 100)
HYBRID_TOP_K = _clamp(_env_int("HYBRID_TOP_K", 20), 4, 50)
RERANK_TOP_N = _clamp(_env_int("RERANK_TOP_N", 5), 1, 20)

# Reranker scores are 0..1 (measured: questions the index covers score
# 0.5-1.0, topics with only related papers ~0.15, unrelated questions <0.07).
# Best paper below RELEVANCE_MIN_SCORE: declined ("no relevant papers") instead
# of answering from unrelated sources.  Below RELEVANCE_WARN_SCORE: answered
# with a notice that only loosely related papers were found.  Papers below
# SOURCE_MIN_SCORE are left out of the answer context and the references.
RELEVANCE_MIN_SCORE = _clamp(_env_float("RELEVANCE_MIN_SCORE", 0.1), 0.0, 1.0)
RELEVANCE_WARN_SCORE = _clamp(_env_float("RELEVANCE_WARN_SCORE", 0.3), 0.0, 1.0)
SOURCE_MIN_SCORE = _clamp(_env_float("SOURCE_MIN_SCORE", 0.05), 0.0, 1.0)
# Papers the answer is written from (= the references listed under it).
ANSWER_SOURCES = _clamp(_env_int("ANSWER_SOURCES", 5), 1, 10)

# Every answer and summary sentence must be supported by those papers
# (search_service/grounding.py): cross-encoder score of the sentence against
# its best source passage >= GROUNDING_MIN_SCORE, and every number and name in
# it must occur in the sources.  Unsupported sentences are not shown.
GROUNDING = _env_bool("GROUNDING", True)
GROUNDING_MIN_SCORE = _clamp(_env_float("GROUNDING_MIN_SCORE", 0.3), 0.0, 1.0)

# "llm": the language model judges whether a question is research
# (search_service/guard.py); "keywords": every keyword in
# search_service/filters.py blocks.
GUARD_MODE = os.getenv("GUARD_MODE", "llm").strip().lower()

# Previous question/answer pairs used to understand follow-up questions.
HISTORY_TURNS = _clamp(_env_int("HISTORY_TURNS", 3), 0, 10)
SUGGEST_FOLLOWUPS = _env_bool("SUGGEST_FOLLOWUPS", True)

# Half precision for the embedding model and reranker of the search service on
# CUDA: about 0.8 GB less GPU memory, same ranking (room for the safety models).
SEARCH_FP16 = _env_bool("SEARCH_FP16", True)


# =====================================================
# Search service: safety models (Qwen3Guard, search_service/safety.py)
# =====================================================

def _env_list(name: str, default: str):
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


# Qwen3Guard-Gen judges each question; Qwen3Guard-Stream checks the answer
# while it is written.  A missing model folder turns that check off.  Devices:
# auto = the GPU if the model fits next to the other models, else the CPU.
SAFETY_INPUT = _env_bool("SAFETY_INPUT", True)
SAFETY_INPUT_MODEL_PATH = os.getenv("SAFETY_INPUT_MODEL_PATH", "/app/models/Qwen3Guard-Gen-0.6B")
SAFETY_INPUT_DEVICE = os.getenv("SAFETY_INPUT_DEVICE", "auto").strip().lower()
SAFETY_OUTPUT = _env_bool("SAFETY_OUTPUT", True)
SAFETY_OUTPUT_MODEL_PATH = os.getenv("SAFETY_OUTPUT_MODEL_PATH", "/app/models/Qwen3Guard-Stream-0.6B")
SAFETY_OUTPUT_DEVICE = os.getenv("SAFETY_OUTPUT_DEVICE", "auto").strip().lower()

# "Unsafe" is always refused.  "Controversial" only in these categories
# (Violent, Sexual Content, Self-Harm, Political, PII, Copyright, Illegal Acts,
# Unethical, Jailbreak).
SAFETY_INPUT_CONTROVERSIAL = _env_list("SAFETY_INPUT_CONTROVERSIAL", "Political,Jailbreak")
SAFETY_OUTPUT_CONTROVERSIAL = _env_list("SAFETY_OUTPUT_CONTROVERSIAL", "")
# An answer is stopped after this many answer tokens in a row are judged unsafe
# (1 = the first one; higher values ignore single-token false alarms).
SAFETY_OUTPUT_MIN_TOKENS = _clamp(_env_int("SAFETY_OUTPUT_MIN_TOKENS", 2), 1, 20)


# =====================================================
# Embedding service
# =====================================================

EMBED_SERVICE_PORT = _clamp(_env_int("EMBED_SERVICE_PORT", 7861), 1, 65535)

# Folder tree the embedding UI can browse; uploads are stored below it.
IMPORT_ROOT = os.getenv("IMPORT_ROOT", "/import")
# Host path of IMPORT_ROOT, shown in the UI so users recognise the folder.
IMPORT_HOST_DIR = os.getenv("IMPORT_HOST_DIR", IMPORT_ROOT)
EMBED_JOB_DIR = os.getenv("EMBED_JOB_DIR", f"{BASE_DATA_DIR}/embedding_jobs")

# Chunks per GPU batch.  Halved automatically on CUDA out-of-memory.
EMBED_BATCH_SIZE = _clamp(_env_int("EMBED_BATCH_SIZE", 128), 8, 1024)
# Half precision for ingest embeddings on CUDA (several times faster).
EMBED_FP16 = _env_bool("EMBED_FP16", True)
# Papers read, embedded and uploaded together.  Memory use depends on this,
# not on the size of the table, so any number of papers can be ingested.
INGEST_BATCH = _clamp(_env_int("INGEST_BATCH", 1000), 100, 20000)
# Chunk length and overlap in tokens (about 4 characters each); most
# abstracts fit in one chunk.
CHUNK_SIZE = _clamp(_env_int("CHUNK_SIZE", 512), 128, 2048)
CHUNK_OVERLAP = _clamp(_env_int("CHUNK_OVERLAP", 80), 0, 512)
MIN_ABSTRACT_CHARS = _clamp(_env_int("MIN_ABSTRACT_CHARS", 50), 0, 2000)
OPTIMIZE_TIMEOUT = _clamp(_env_int("OPTIMIZE_TIMEOUT", 900), 30, 7200)

# MariaDB reads the MySQL MyISAM table files (.frm/.MYD/.MYI).  Its data
# directory is shared with the embedding service (MARIADB_DATADIR) so table
# files can be staged where the server can open them.
MARIADB_HOST = os.getenv("MARIADB_HOST", "mariadb")
MARIADB_PORT = _clamp(_env_int("MARIADB_PORT", 3306), 1, 65535)
MARIADB_USER = os.getenv("MARIADB_USER", "root")
MARIADB_PASSWORD = os.getenv("MARIADB_PASSWORD", "")
MARIADB_DATADIR = os.getenv("MARIADB_DATADIR", "/mariadb_data")
MARIADB_STAGING_DB = os.getenv("MARIADB_STAGING_DB", "scopus_import")
