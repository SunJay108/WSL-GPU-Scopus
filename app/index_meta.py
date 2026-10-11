"""
Shared description of the live search index.

The embedding service publishes a new index by switching the Qdrant alias,
replacing bm25.pkl and finally writing index_meta.json.  The Scopus UI checks
index_signature() before each query and reloads its BM25/vector handles when
the files change, so no restart is needed after an ingest.
"""

import json
import os
import tempfile

from config import BM25_PATH, INDEX_META_PATH


def read_index_meta():
    """Return the published index description, or None if there is none."""
    try:
        with open(INDEX_META_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        print(f"⚠️ Could not read {INDEX_META_PATH}: {exc}")
        return None


def write_index_meta(meta: dict) -> None:
    atomic_write_json(INDEX_META_PATH, meta)


def atomic_write_json(path: str, data) -> None:
    """Write JSON next to `path` first, then rename, so readers never see half a file."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.chmod(tmp_path, 0o644)  # mkstemp creates 0600; host users must be able to copy it
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def index_signature():
    """Cheap change detector: modification times of the meta file and BM25 pickle."""
    signature = []
    for path in (INDEX_META_PATH, BM25_PATH):
        try:
            signature.append(os.stat(path).st_mtime_ns)
        except OSError:
            signature.append(None)
    return tuple(signature)
