"""
Remove every Python package the embedding service does not need from the
virtual environment (used while building its image from the search app's
image).  What stays: the packages below and everything they depend on,
including requested extras.
"""

import importlib.metadata as md
import re
import subprocess
import sys

from packaging.requirements import Requirement

KEEP = ["torch", "sentence-transformers", "qdrant-client", "fastapi", "uvicorn", "PyMySQL", "rispy",
        "pip", "setuptools", "packaging"]


def norm(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def main():
    dists = {norm(d.metadata["Name"]): d for d in md.distributions()}
    keep, stack = set(), [(norm(n), frozenset()) for n in KEEP]
    seen = set()
    while stack:
        name, extras = stack.pop()
        if (name, extras) in seen or name not in dists:
            continue
        seen.add((name, extras))
        keep.add(name)
        for line in dists[name].requires or []:
            req = Requirement(line)
            if req.marker and not any(req.marker.evaluate({"extra": e}) for e in (extras | {""})):
                continue
            stack.append((norm(req.name), frozenset(req.extras)))

    remove = sorted(n for n in dists if n not in keep)
    print(f"Keeping {len(keep)} packages, removing {len(remove)}:", " ".join(remove))
    if remove:
        subprocess.check_call([sys.executable, "-m", "pip", "uninstall", "-y", *remove])


if __name__ == "__main__":
    main()
