#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/offline_bundle"
mkdir -p "$OUT/images"

# Ensure the exact images referenced by docker-compose.yml exist locally.
docker image inspect scopus-ai-gpu:1.1 >/dev/null
docker image inspect qdrant/qdrant:v1.12.6 >/dev/null
# Language model server image (pinned in docker-compose.yml).
LLM_IMAGE=$(grep -o 'scopus-llm:[^ ]*' "$ROOT/docker-compose.yml" | head -1)
docker image inspect "$LLM_IMAGE" >/dev/null

echo 'Saving Docker images...'
# The app image and the language model server image share almost all layers:
# one archive stores them once.
docker save scopus-ai-gpu:1.1 "$LLM_IMAGE" -o "$OUT/images/scopus-ai-gpu-1.1.tar"
docker save qdrant/qdrant:v1.12.6 -o "$OUT/images/qdrant-1.12.6.tar"

echo 'Copying deployment files...'
cp "$ROOT/docker-compose.yml" "$OUT/"
cp "$ROOT/.env.example" "$OUT/"
[ -f "$ROOT/.env" ] && cp "$ROOT/.env" "$OUT/.env"
cp -a "$ROOT/models" "$OUT/"
# docker-compose.yml bind-mounts ./app over the image's code, so the bundle
# needs the application source as well as app/data.
mkdir -p "$OUT/app"
tar -C "$ROOT/app" --exclude='__pycache__' --exclude='*.pyc' --exclude='./data/uploads' -cf - . | tar -C "$OUT/app" -xf -
cp -a "$ROOT/qdrant_data" "$OUT/"
# Index bundles from the embedding PC are copied in here later.
mkdir -p "$OUT/bundles"
cp "$ROOT/scripts/import_offline.sh" "$OUT/"
cp "$ROOT/scripts/verify_gpu.sh" "$OUT/"

cat > "$OUT/START_OFFLINE.txt" <<'TXT'
1. Confirm Docker Engine + NVIDIA Container Toolkit already work on the offline WSL machine.
2. cd to this bundle.
3. chmod +x import_offline.sh verify_gpu.sh
4. ./import_offline.sh
5. Copy index bundles from the embedding PC into bundles/ and import them: admin panel > Index
6. Scopus search UI:  http://localhost:7860
7. ./verify_gpu.sh
TXT

echo "Offline bundle created at: $OUT"
