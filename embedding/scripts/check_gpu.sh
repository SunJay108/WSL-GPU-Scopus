#!/usr/bin/env bash
# Check that the embedding image sees the GPU and that the model loads and
# embeds (prints the speed).  Run from the embedding folder.
set -euo pipefail
cd "$(dirname "$0")/.."

docker run --rm -i --gpus all -v "$PWD/models:/app/models:ro" -v "$PWD/app:/app/app:ro" \
  -e HF_HUB_OFFLINE=1 -e TRANSFORMERS_OFFLINE=1 scopus-embedding:1.0 python - <<'EOF'
import subprocess, time
import torch
from core import embedding

print(subprocess.run(["nvidia-smi", "--query-gpu=name,driver_version,memory.total",
                      "--format=csv,noheader"], capture_output=True, text=True).stdout.strip() or "nvidia-smi: no GPU")
print(f"PyTorch {torch.__version__}, CUDA available: {torch.cuda.is_available()}")
model = embedding.load("cuda" if torch.cuda.is_available() else "cpu")
texts = ["Title: A study of graph neural networks\nAbstract:\n" + "Graph neural networks learn on graphs. " * 40] * 1024
embedding.embed_passages(model, texts[:64])
start = time.time()
embedding.embed_passages(model, texts)
print(f"Embedding on {embedding.device_label(model)}: {len(texts) / (time.time() - start):,.0f} passages/s")
EOF
