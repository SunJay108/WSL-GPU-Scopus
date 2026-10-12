#!/usr/bin/env bash
# Download the Qwen3Guard safety models into ./models (online PC only).
#
#   ./scripts/download_safety_models.sh
#
# Qwen3Guard-Gen-0.6B checks questions, Qwen3Guard-Stream-0.6B checks answers
# while they are written (Apache-2.0, about 1.5 GB each).  Uses the app image,
# so nothing has to be installed on the host; an interrupted download resumes.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p models

docker run --rm --user "$(id -u):$(id -g)" \
    -e HF_HOME=/tmp/hf -e HF_HUB_OFFLINE=0 -e HF_HUB_DISABLE_XET=1 -e HF_HUB_DOWNLOAD_TIMEOUT=60 \
    -v "$PWD/models:/models" --entrypoint python scopus-ai-gpu:1.1 -c "
from huggingface_hub import snapshot_download
for repo in ('Qwen/Qwen3Guard-Gen-0.6B', 'Qwen/Qwen3Guard-Stream-0.6B'):
    path = snapshot_download(repo, local_dir='/models/' + repo.split('/')[1])
    print('downloaded', repo, '->', path)
"
rm -rf models/Qwen3Guard-*/.cache
du -sh models/Qwen3Guard-*
