#!/usr/bin/env bash
# Save the Docker images of the embedding service into one file for the
# offline embedding PC (run on the PC with internet access).
#
#   scripts/save_images.sh   ->  images/scopus-embedding-images.tar (+ .sha256)
set -euo pipefail
cd "$(dirname "$0")/.."

IMAGES=(scopus-embedding:1.0 qdrant/qdrant:v1.12.6 mariadb:10.11.19)

for image in "${IMAGES[@]}"; do
  docker image inspect "$image" >/dev/null 2>&1 || {
    echo "Image $image not found. Build it (docker compose build) or pull it (docker compose pull) first."
    exit 1
  }
done

mkdir -p images
echo "Saving ${IMAGES[*]} ..."
docker save -o images/scopus-embedding-images.tar "${IMAGES[@]}"
(cd images && sha256sum scopus-embedding-images.tar > scopus-embedding-images.tar.sha256)
ls -lh images
echo "Copy this folder (with models/ and images/) to the offline PC and run scripts/load_images.sh there."
