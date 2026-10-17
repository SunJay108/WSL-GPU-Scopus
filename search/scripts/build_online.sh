#!/usr/bin/env bash
# Build both images of the search app from the Dockerfile (needs Internet; no existing
# image is used) and start the stack.
set -euo pipefail
cd "$(dirname "$0")/.."
cp -n .env.example .env 2>/dev/null || true

echo 'Downloading the llama.cpp source archive...'
./scripts/get_llama_server_source.sh

echo 'Building scopus-llm:b10991 and scopus-ai-gpu:1.1 (about 45 minutes)...'
docker compose build

echo 'Pulling Qdrant...'
docker pull qdrant/qdrant:v1.12.6

echo 'Starting stack...'
docker compose up -d

echo 'Done.'
echo '  Scopus search UI:  http://localhost:7860'
echo '  Import an index bundle: admin panel > Index (bundles from the embedding app in ./bundles)'
echo '  The embedding app can now be built: cd ../embedding && docker compose build'
