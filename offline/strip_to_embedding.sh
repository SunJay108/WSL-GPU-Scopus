#!/usr/bin/env bash
# Run by make_packages.ps1 as root in the temporary copy that becomes the EMBEDDING package:
# removes the search app (with its accounts and index) and its images, removes the input
# data and any index of the embedding app, then starts the embedding app and checks it.
set -uo pipefail
H=/home/${1:-adonis}
for i in $(seq 1 90); do docker info >/dev/null 2>&1 && break; sleep 2; done; echo "docker ready"

echo "--- remove the search app and its images"
rm -rf "$H/Scopus"
docker rmi scopus-llm:b10991 scopus-ai-gpu:1.1 scopus-ai-gpu:1.0 2>&1 | tail -1
docker image prune -f >/dev/null           # dangling layers only (never -a: no container runs)
docker builder prune -af >/dev/null

echo "--- empty embedding app: no input data, no index, no bundles, no job state"
E="$H/ScopusEmbedding"
find "$E/data/import" "$E/data/exports" -mindepth 1 ! -name .gitkeep -exec rm -rf {} + 2>/dev/null
rm -rf "$E/data/qdrant" "$E/data/state/index_meta.json" "$E/data/state/jobs"
docker images --format '{{.Repository}}:{{.Tag}} {{.Size}}'; docker system df | head -3
ls -a "$H" "$E/data/import" "$E/data/exports"

echo "--- check the embedding app"
cd "$E" && docker compose up -d --no-build 2>&1 | tail -1
for i in $(seq 1 60); do [ "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:7861/api/auth/state)" = 200 ] && break; sleep 2; done
echo "embedding UI: http $(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:7861/api/auth/state)"
sleep 5
docker exec embed-service python -c "from core.index_meta import read_index_meta; import urllib.request, json; print('index meta:', read_index_meta()); print('qdrant collections:', json.load(urllib.request.urlopen('http://qdrant:6333/collections'))['result']['collections'])"
bash scripts/check_gpu.sh 2>&1 | grep -vE "Loading weights|Warning"
docker compose down 2>&1 | tail -1
rm -rf "$E/data/qdrant"                     # created by the check; Qdrant starts empty
echo "containers left: $(docker ps -aq | wc -l)"
