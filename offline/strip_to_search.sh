#!/usr/bin/env bash
# Run by make_packages.ps1 as root in the temporary copy that becomes the SEARCH package:
# removes the embedding app, its images and volume, then starts the search app and checks it.
set -uo pipefail
H=/home/${1:-adonis}
for i in $(seq 1 90); do docker info >/dev/null 2>&1 && break; sleep 2; done; echo "docker ready"

echo "--- remove the embedding app and its images"
rm -rf "$H/ScopusEmbedding"
docker volume rm scopus-embedding_mariadb_data 2>&1 | tail -1
docker rmi scopus-embedding:1.0 mariadb:10.11.19 2>&1 | tail -1
docker image prune -f >/dev/null           # dangling layers only (never -a: no container runs)
docker builder prune -af >/dev/null
find "$H/Scopus/bundles" -mindepth 1 ! -name .gitkeep -exec rm -rf {} + 2>/dev/null
docker images --format '{{.Repository}}:{{.Tag}} {{.Size}}'; docker system df | head -3; ls -a "$H"

echo "--- check the search app"
cd "$H/Scopus" && docker compose up -d --no-build 2>&1 | tail -1
for i in $(seq 1 90); do curl -s http://127.0.0.1:7860/api/health | grep -q '"status":"ok"' && break; sleep 5; done
echo "search health: $(curl -s http://127.0.0.1:7860/api/health)"
docker exec scopus-ai-gpu python import_index.py
python3 -c "import json; m=json.load(open('$H/Scopus/app/data/storage/index_meta.json')); print('index:', m['version'], m['papers'], 'papers')"
docker exec scopus-ai-gpu nvidia-smi --query-gpu=name,memory.used --format=csv,noheader
docker compose down 2>&1 | tail -1
echo "containers left: $(docker ps -aq | wc -l)"
