#!/usr/bin/env bash
# Load the images saved by save_images.sh on the offline embedding PC and
# check that the GPU is usable.
set -euo pipefail
cd "$(dirname "$0")/.."

(cd images && sha256sum -c scopus-embedding-images.tar.sha256)
docker load -i images/scopus-embedding-images.tar
scripts/check_gpu.sh
