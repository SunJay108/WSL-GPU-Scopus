#!/usr/bin/env bash
# Download the llama.cpp source archive used by Dockerfile.llm (online PC only).
# Resumes interrupted downloads, so it also works on a slow or unstable line.
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION=$(sed -n 's/^ARG LLAMA_CPP_VERSION=//p' Dockerfile.llm)
FILE="third_party/llama.cpp-${VERSION}.tar.gz"
mkdir -p third_party

if [[ -s "$FILE" ]] && tar tzf "$FILE" >/dev/null 2>&1; then
    echo "$FILE already present."
    exit 0
fi
for attempt in $(seq 1 20); do
    curl -fSL -C - --connect-timeout 20 --max-time 600 -o "$FILE" \
        "https://github.com/ggml-org/llama.cpp/archive/refs/tags/${VERSION}.tar.gz" && break
    echo "Download interrupted (attempt $attempt); resuming..."
    sleep 3
done
tar tzf "$FILE" >/dev/null
echo "Downloaded $FILE ($(du -h "$FILE" | cut -f1))."
