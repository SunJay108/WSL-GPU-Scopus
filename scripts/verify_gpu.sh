#!/usr/bin/env bash
set -euo pipefail

echo '=== WSL GPU ==='
nvidia-smi || /usr/lib/wsl/lib/nvidia-smi

echo
echo '=== Docker GPU ==='
docker exec scopus-ai-gpu nvidia-smi

echo
echo '=== llama.cpp GPU offload support ==='
docker exec scopus-ai-gpu python3 -c 'from llama_cpp import llama_cpp; print("llama_supports_gpu_offload =", llama_cpp.llama_supports_gpu_offload())'

echo
echo '=== PyTorch CUDA (Scopus UI) ==='
docker exec scopus-ai-gpu python3 -c 'import torch; print("torch.cuda.is_available =", torch.cuda.is_available()); print("GPU =", torch.cuda.get_device_name(0) if torch.cuda.is_available() else "NONE")'

echo
echo '=== PyTorch CUDA (embedding service) ==='
docker exec scopus-embedding python3 -c 'import torch; print("torch.cuda.is_available =", torch.cuda.is_available()); print("GPU =", torch.cuda.get_device_name(0) if torch.cuda.is_available() else "NONE")'
