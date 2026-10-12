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
LLM_PATH = os.getenv("LLM_PATH", "/app/models/qwen2.5-3b-instruct-q4_k_m.gguf")

# "cuda" is preferred; both services fall back to the CPU when CUDA is unavailable.
EMBED_DEVICE = os.getenv("EMBED_DEVICE", "cuda").strip().lower()
RERANK_DEVICE = os.getenv("RERANK_DEVICE", "cuda").strip().lower()

BASE_DATA_DIR = os.getenv("BASE_DATA_DIR", "/app/app/data")
STORAGE_PATH = os.getenv("STORAGE_PATH", f"{BASE_DATA_DIR}/storage")

QDRANT_URL = os.getenv("QDRANT_URL", "http://qdrant:6333")
QDRANT_TIMEOUT = _clamp(_env_int("QDRANT_TIMEOUT", 300), 30, 1800)

# Written by the embedding service, read by the search UI.  QDRANT_COLLECTION
# is a Qdrant alias that always points at the live collection, so a rebuilt
# index is swapped in atomically.
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "papers")
BM25_PATH = os.getenv("BM25_PATH", f"{STORAGE_PATH}/bm25.pkl")
INDEX_META_PATH = os.getenv("INDEX_META_PATH", f"{STORAGE_PATH}/index_meta.json")

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

LLM_CTX = _clamp(_env_int("LLM_CTX", 4096), 3072, 8192)
LLM_BATCH = _clamp(_env_int("LLM_BATCH", 256), 32, 1024)
LLM_THREADS = _clamp(_env_int("LLM_THREADS", 4), 1, 32)
# -1 offloads all layers to the GPU; other negative values mean the same.
_gpu_layers = _env_int("LLM_GPU_LAYERS", -1)
LLM_GPU_LAYERS = -1 if _gpu_layers < 0 else min(_gpu_layers, 200)

# Tokens per generation call.  Unfinished answers are continued automatically
# (up to LLM_MAX_CONTINUATIONS calls), so this does not limit the final answer.
LLM_MAX_NEW_TOKENS = _clamp(_env_int("LLM_MAX_NEW_TOKENS", 512), 128, 2048)
LLM_MAX_CONTINUATIONS = _clamp(_env_int("LLM_MAX_CONTINUATIONS", 6), 1, 12)

LLM_TEMPERATURE = _clamp(_env_float("LLM_TEMPERATURE", 0.10), 0.0, 1.5)
LLM_REPEAT_PENALTY = _clamp(_env_float("LLM_REPEAT_PENALTY", 1.18), 1.0, 1.5)
LLM_TOP_P = _clamp(_env_float("LLM_TOP_P", 0.90), 0.05, 1.0)
LLM_TOP_K = _clamp(_env_int("LLM_TOP_K", 40), 1, 200)

SUMMARY_MAX_CONTINUATIONS = _clamp(_env_int("SUMMARY_MAX_CONTINUATIONS", 2), 1, 4)
SUMMARY_MAX_SENTENCES = _clamp(_env_int("SUMMARY_MAX_SENTENCES", 4), 2, 6)
SUMMARY_MAX_WORDS = _clamp(_env_int("SUMMARY_MAX_WORDS", 140), 60, 300)

# Characters of the unfinished answer shown to the model when continuing it.
CONTINUATION_TAIL_CHARS = _clamp(_env_int("CONTINUATION_TAIL_CHARS", 3000), 1000, 8000)

# Marker requested from the model when a response is truly complete.
END_MARKER = "<END_OF_ANSWER>"


# =====================================================
# Search service: retrieval, guard, chat
# =====================================================

VECTOR_TOP_K = _clamp(_env_int("VECTOR_TOP_K", 12), 4, 50)
HYBRID_TOP_K = _clamp(_env_int("HYBRID_TOP_K", 10), 4, 30)
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

# "llm": the language model judges whether a question is research
# (search_service/guard.py); "keywords": every keyword in
# search_service/filters.py blocks.
GUARD_MODE = os.getenv("GUARD_MODE", "llm").strip().lower()

# Previous question/answer pairs used to understand follow-up questions.
HISTORY_TURNS = _clamp(_env_int("HISTORY_TURNS", 3), 0, 10)
SUGGEST_FOLLOWUPS = _env_bool("SUGGEST_FOLLOWUPS", True)


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
