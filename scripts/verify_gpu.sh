#!/usr/bin/env bash
set -euo pipefail

echo '=== WSL GPU ==='
nvidia-smi || /usr/lib/wsl/lib/nvidia-smi

echo
echo '=== Docker GPU ==='
docker exec scopus-ai-gpu nvidia-smi

echo
echo '=== Language model server (llama.cpp) ==='
docker exec scopus-llm python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8080/health').read().decode())"
# Speed of the last requests: hundreds of tokens per second or more = GPU.
docker logs scopus-llm 2>&1 | grep -E "prompt eval time|  eval time" | tail -2

echo
echo '=== PyTorch CUDA (Scopus UI) ==='
docker exec scopus-ai-gpu python3 -c 'import torch; print("torch.cuda.is_available =", torch.cuda.is_available()); print("GPU =", torch.cuda.get_device_name(0) if torch.cuda.is_available() else "NONE")'

echo
echo '=== PyTorch CUDA (embedding service) ==='
docker exec scopus-embedding python3 -c 'import torch; print("torch.cuda.is_available =", torch.cuda.is_available()); print("GPU =", torch.cuda.get_device_name(0) if torch.cuda.is_available() else "NONE")'
