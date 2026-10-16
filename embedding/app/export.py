"""
Command-line export (the web UI of the embedding service does the same).

    python export.py                         # bundle of the current index in /exports
    python export.py --list                  # bundles in /exports
    python export.py --verify /exports/NAME  # check a (copied) bundle against its checksums

Do not export while an ingest is running.
"""

import argparse
import sys
import time

from core.config import EXPORT_HOST_DIR
from embedding_service import exporter


def main():
    parser = argparse.ArgumentParser(description="Export the search index for the search app.")
    parser.add_argument("--list", action="store_true", help="list the bundles")
    parser.add_argument("--verify", metavar="FOLDER", help="check the files of a bundle")
    args = parser.parse_args()

    if args.list:
        for b in exporter.list_bundles():
            print(f"{b['name']}  {b['papers'] or 0:>10,} papers  {b['chunks'] or 0:>10,} chunks  "
                  f"{b['size'] / 2**30:6.2f} GB  {b['created_at']}")
        return 0

    if args.verify:
        problems = exporter.verify_bundle(args.verify)
        for problem in problems:
            print(f"❌ {problem}")
        print("✅ Bundle is complete and undamaged." if not problems else "Bundle is NOT usable.")
        return 1 if problems else 0

    started = time.time()
    try:
        bundle = exporter.export_bundle(lambda step: print(step), username="cli")
    except RuntimeError as exc:
        print(f"❌ {exc}")
        return 1
    print(f"✅ {EXPORT_HOST_DIR.rstrip('/')}/{bundle['name']}  "
          f"({bundle['size'] / 2**30:.2f} GB, {time.time() - started:.0f} s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
