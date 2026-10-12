"""
Command-line ingest (the web UI of the embedding service does the same).

    python3 ingest.py /import/mysql
    python3 ingest.py /import/ris_index --mode append
"""

import argparse
import sys

from core.config import IMPORT_ROOT, MARIADB_STAGING_DB
from embedding_service.errors import IngestCancelled, IngestError
from embedding_service.pipeline import run_ingest


def main():
    parser = argparse.ArgumentParser(description="Build the Scopus AI search index from a data folder.")
    parser.add_argument("folder", nargs="?", default=f"{IMPORT_ROOT}/mysql",
                        help="folder with MySQL MyISAM files (.frm/.MYD/.MYI) and/or .ris files")
    parser.add_argument("--mode", choices=["replace", "append"], default="replace")
    parser.add_argument("--tables", nargs="*", help="only these table names (default: all)")
    parser.add_argument("--no-ris", action="store_true", help="ignore .ris files in the folder")
    args = parser.parse_args()

    try:
        summary = run_ingest(
            folder=args.folder,
            tables=args.tables or None,
            include_ris=not args.no_ris,
            mode=args.mode,
            # Separate staging database so a CLI run cannot clash with the web service.
            staging_db=f"{MARIADB_STAGING_DB}_cli",
        )
    except IngestError as exc:
        print(f"\n❌ {exc}")
        return 1
    except (IngestCancelled, KeyboardInterrupt):
        print("\n⚠️ Cancelled; the current index is unchanged.")
        return 130

    print("\n✅ Index build complete:")
    for key, value in summary.items():
        print(f"   {key}: {value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
