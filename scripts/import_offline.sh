#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

echo 'Loading application image...'
docker load -i images/scopus-ai-gpu-1.1.tar

echo 'Loading Qdrant image...'
docker load -i images/qdrant-1.12.6.tar

echo 'Loading MariaDB image...'
docker load -i images/mariadb-10.11.19.tar

cp -n .env.example .env 2>/dev/null || true

echo 'Starting offline stack without pulling/building...'
docker compose up -d --no-build --pull never

echo 'Done.'
echo '  Embedding service: http://localhost:7861'
echo '  Scopus search UI:  http://localhost:7860'
