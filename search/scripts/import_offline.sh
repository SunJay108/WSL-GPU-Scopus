#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

echo 'Loading application and language model server images...'
docker load -i images/scopus-ai-gpu-1.1.tar

echo 'Loading Qdrant image...'
docker load -i images/qdrant-1.12.6.tar

cp -n .env.example .env 2>/dev/null || true

echo 'Starting offline stack without pulling/building...'
docker compose up -d --no-build --pull never

echo 'Done.'
echo '  Import an index bundle: copy it into bundles/, then admin panel > Index'
echo '  Scopus search UI:  http://localhost:7860'
