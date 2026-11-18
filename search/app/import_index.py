"""
Command-line index import (the admin panel's Index tab does the same).

    docker exec -it scopus-ai-gpu python import_index.py                 # bundles in the bundles folder
    docker exec -it scopus-ai-gpu python import_index.py NAME            # check NAME; it replaces the index
    docker exec -it scopus-ai-gpu python import_index.py NAME --low-memory
    docker exec -it scopus-ai-gpu python import_index.py NAME --add      # add its papers to the index
    docker exec -it scopus-ai-gpu python import_index.py NAME --verify   # only check the files

The running search service notices the new index within seconds and warms it up.
"""

import argparse
import sys
import time

from core import audit, db
from core.config import BUNDLES_HOST_DIR
from search_service import index_import


def _progress():
    last = {"t": 0.0, "step": None}

    def report(step, done=None, total=None):
        now = time.time()
        if step != last["step"] or now - last["t"] >= 5:
            suffix = f"  {100 * done / total:5.1f}%" if total and done is not None else ""
            print(f"{time.strftime('%H:%M:%S')}  {step}{suffix}", flush=True)
            last.update(t=now, step=step)

    return report


def main():
    parser = argparse.ArgumentParser(description="Import an index bundle made by the embedding app.")
    parser.add_argument("bundle", nargs="?", help=f"bundle folder name in {BUNDLES_HOST_DIR}")
    parser.add_argument("--verify", action="store_true", help="only check the files (SHA-256), do not import")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--low-memory", action="store_true",
                      help="delete the current index first: half the memory, no search during the import")
    mode.add_argument("--add", action="store_true",
                      help="add the bundle's papers to the current index instead of replacing it "
                           "(papers already in it are replaced by the bundle's copy)")
    args = parser.parse_args()

    if not args.bundle:
        bundles = index_import.list_bundles()
        print(f"Bundles in {BUNDLES_HOST_DIR}:" if bundles else f"No bundles in {BUNDLES_HOST_DIR}.")
        width = max(len(b["name"]) for b in bundles) if bundles else 0
        for b in bundles:
            state = ("cannot be imported: " + "; ".join(b["problems"]) if b["problems"]
                     else "in use" if b["in_use"] else "ready")
            print(f"  {b['name']:<{width}}  {b['papers'] or 0:>10,} papers  {b['size'] / 2**30:6.2f} GB  {state}")
        return 0

    try:
        if args.verify:
            path = index_import.bundle_path(args.bundle)
            manifest = index_import.read_manifest(path)
            problems = index_import.check_manifest(manifest) + index_import.check_files(path, manifest)
            if problems:
                raise index_import.BundleError("; ".join(problems))
            index_import.verify_checksums(path, manifest, lambda d, t: _report("Checking the files (SHA-256)…", d, t))
            print("✅ The bundle is complete and undamaged.")
            return 0
        db.init()
        audit.log("index_import_start", detail={"bundle": args.bundle, "low_memory": args.low_memory,
                                                "add": args.add}, username="command line")
        summary = index_import.import_bundle(args.bundle, low_memory=args.low_memory, report=_report,
                                             username="command line", add=args.add)
    except (index_import.BundleError, index_import.ImportBusyError) as exc:
        print(f"❌ {exc}")
        if not args.verify:
            audit.log("index_import_failed", detail={"bundle": args.bundle, "error": str(exc)},
                      username="command line")
        return 1
    audit.log("index_import_completed", detail=summary, username="command line")
    if summary["mode"] == "add":
        print(f"✅ Added {summary['papers_new']:,} new papers and replaced {summary['papers_replaced']:,} in "
              f"{summary['seconds']} s; the index now has {summary['index_papers'] or 0:,} papers "
              f"({summary['index_chunks']:,} chunks).")
    else:
        print(f"✅ Imported {summary['papers'] or 0:,} papers ({summary['chunks'] or 0:,} chunks) in "
              f"{summary['seconds']} s; the search uses the new index.")
    return 0


_report = _progress()

if __name__ == "__main__":
    sys.exit(main())
