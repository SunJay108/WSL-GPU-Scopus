#!/usr/bin/env bash
# Build scopus-ai-gpu:1.1 on top of an existing scopus-ai-gpu:1.0 (needs Internet once).
# Use ./scripts/build_online.sh instead to build everything from scratch.
set -euo pipefail
cd "$(dirname "$0")/.."

docker image inspect scopus-ai-gpu:1.0 >/dev/null
docker build -f Dockerfile.upgrade -t scopus-ai-gpu:1.1 .
docker pull mariadb:10.11.19

echo 'Done. Start with: docker compose up -d'
