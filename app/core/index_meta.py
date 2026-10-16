"""
Shared description of the live search index.

The embedding service publishes a new index by switching the Qdrant alias
and then writing index_meta.json.  The Scopus UI searches through the alias,
so it uses a new index at once; index_signature() tells it when to re-read
the index description.
"""

import json
import os
import tempfile

from .config import INDEX_META_PATH


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
    """Cheap change detector: modification time of the index description."""
    try:
        return os.stat(INDEX_META_PATH).st_mtime_ns
    except OSError:
        return None
