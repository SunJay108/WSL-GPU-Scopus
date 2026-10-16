"""
Settings of the embedding service, read from environment variables
(docker-compose.yml passes the values from .env).  Numbers are clamped to
safe ranges so a typo cannot break the application.
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


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _clamp(value, minimum, maximum):
    return max(minimum, min(value, maximum))


# =====================================================
# Folders (inside the container; see docker-compose.yml)
# =====================================================

# Folder tree the UI can browse (MySQL table folders, RIS files); uploads are
# stored below it.  IMPORT_HOST_DIR is the same folder as seen on the host,
# shown in the UI so users recognise it.
IMPORT_ROOT = os.getenv("IMPORT_ROOT", "/import")
IMPORT_HOST_DIR = os.getenv("IMPORT_HOST_DIR", IMPORT_ROOT)

# Hand-over bundles for the search app (Qdrant snapshot + index description).
EXPORT_DIR = os.getenv("EXPORT_DIR", "/exports")
EXPORT_HOST_DIR = os.getenv("EXPORT_HOST_DIR", EXPORT_DIR)
# Where Qdrant writes snapshots, as seen by this service (a folder inside
# EXPORT_DIR, so a finished snapshot is moved into its bundle instantly).
QDRANT_SNAPSHOTS_DIR = os.getenv("QDRANT_SNAPSHOTS_DIR", f"{EXPORT_DIR}/.snapshots")

# Accounts, activity log, job state and the index description.
STATE_DIR = os.getenv("STATE_DIR", "/state")
ACCOUNTS_DB_PATH = os.getenv("ACCOUNTS_DB_PATH", f"{STATE_DIR}/accounts.sqlite3")
EMBED_JOB_DIR = os.getenv("EMBED_JOB_DIR", f"{STATE_DIR}/jobs")
INDEX_META_PATH = os.getenv("INDEX_META_PATH", f"{STATE_DIR}/index_meta.json")


# =====================================================
# Accounts (only administrators can use the service)
# =====================================================

# The first account always becomes an administrator; with ALLOW_SIGNUP=false
# further accounts are added with users.py only.
ALLOW_SIGNUP = _env_bool("ALLOW_SIGNUP", False)
SESSION_DAYS = _clamp(_env_int("SESSION_DAYS", 7), 1, 365)
EMBED_SERVICE_PORT = _clamp(_env_int("EMBED_SERVICE_PORT", 7861), 1, 65535)


# =====================================================
# Index (Qdrant)
# =====================================================

QDRANT_URL = os.getenv("QDRANT_URL", "http://qdrant:6333")
QDRANT_TIMEOUT = _clamp(_env_int("QDRANT_TIMEOUT", 300), 30, 1800)
# Alias of the current collection; a new index is swapped in atomically.
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "papers")
# Longest wait (seconds) for Qdrant to finish the vector index after an ingest
# and before an export.
OPTIMIZE_TIMEOUT = _clamp(_env_int("OPTIMIZE_TIMEOUT", 7200), 30, 86400)


# =====================================================
# Embedding
# =====================================================

EMBED_MODEL_PATH = os.getenv("EMBED_MODEL_PATH", "/app/models/multilingual-e5-small")
# "cuda" is preferred; falls back to the CPU when CUDA is unavailable.
EMBED_DEVICE = os.getenv("EMBED_DEVICE", "cuda").strip().lower()
# Chunks per GPU batch.  Halved automatically on CUDA out-of-memory.
EMBED_BATCH_SIZE = _clamp(_env_int("EMBED_BATCH_SIZE", 128), 8, 1024)
# Half precision on CUDA (several times faster, same ranking).
EMBED_FP16 = _env_bool("EMBED_FP16", True)
# Papers read, embedded and uploaded together.  Memory use depends on this,
# not on the size of the table, so any number of papers can be ingested.
INGEST_BATCH = _clamp(_env_int("INGEST_BATCH", 1000), 100, 20000)
# Chunk length and overlap in tokens (about 4 characters each); most
# abstracts fit in one chunk.
CHUNK_SIZE = _clamp(_env_int("CHUNK_SIZE", 512), 128, 2048)
CHUNK_OVERLAP = _clamp(_env_int("CHUNK_OVERLAP", 80), 0, 512)
MIN_ABSTRACT_CHARS = _clamp(_env_int("MIN_ABSTRACT_CHARS", 50), 0, 2000)
# Papers looked up again after an ingest to check that they are found.
QUALITY_SAMPLE = _clamp(_env_int("QUALITY_SAMPLE", 100), 0, 2000)


# =====================================================
# MariaDB (reads the MySQL MyISAM table files)
# =====================================================

# Its data directory is shared with this service (MARIADB_DATADIR) so table
# files (.frm/.MYD/.MYI) can be staged where the server can open them.
MARIADB_HOST = os.getenv("MARIADB_HOST", "mariadb")
MARIADB_PORT = _clamp(_env_int("MARIADB_PORT", 3306), 1, 65535)
MARIADB_USER = os.getenv("MARIADB_USER", "root")
MARIADB_PASSWORD = os.getenv("MARIADB_PASSWORD", "")
MARIADB_DATADIR = os.getenv("MARIADB_DATADIR", "/mariadb_data")
MARIADB_STAGING_DB = os.getenv("MARIADB_STAGING_DB", "scopus_import")
