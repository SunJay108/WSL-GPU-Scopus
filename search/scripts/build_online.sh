#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
cp -n .env.example .env 2>/dev/null || true

echo 'Building the GPU image of the search app (the embedding app builds its image from it)...'
docker compose build --no-cache scopus-ai

echo 'Building the language model server (llama.cpp) and pulling Qdrant...'
docker pull qdrant/qdrant:v1.12.6
./scripts/get_llama_server_source.sh
docker compose build llm

echo 'Starting stack...'
docker compose up -d

echo 'Done.'
echo '  Import an index bundle: admin panel > Index (bundles from the embedding app in ./bundles)'
echo '  Scopus search UI:  http://localhost:7860'
