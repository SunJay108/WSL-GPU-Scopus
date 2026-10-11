#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
cp -n .env.example .env 2>/dev/null || true

echo 'Building GPU image (used by both the Scopus UI and the embedding service)...'
docker compose build --no-cache scopus-ai

echo 'Pulling/pinning Qdrant and MariaDB images...'
docker pull qdrant/qdrant:v1.12.6
docker pull mariadb:10.11.19

echo 'Starting stack...'
docker compose up -d

echo 'Done.'
echo '  Embedding service: http://localhost:7861  (select the MySQL data folder and ingest)'
echo '  Scopus search UI:  http://localhost:7860'
