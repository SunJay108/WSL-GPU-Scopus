"""
Description of the current index (index_meta.json): papers, chunks, sources,
embedding model and index layout.  Written after every ingest and included
in each export bundle, so the search app knows what it imports.
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
