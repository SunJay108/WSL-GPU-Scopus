#!/usr/bin/env bash
# Headless ingest; the embedding service UI (http://localhost:7861) does the same.
#   ./scripts/ingest.sh                       -> IMPORT_DIR/mysql
#   ./scripts/ingest.sh mysql --mode append   -> folder relative to IMPORT_DIR
set -euo pipefail
cd "$(dirname "$0")/.."
FOLDER="${1:-mysql}"
shift || true
docker compose up -d qdrant mariadb
sleep 5
docker compose run --rm --no-deps scopus-embedding python3 ingest.py "/import/${FOLDER#/}" "$@"
