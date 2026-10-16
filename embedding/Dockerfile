# Image of the embedding service.
#
# The Python packages are taken from the search app's image (built from the
# search app's Dockerfile, search/Dockerfile), and everything the embedding service
# does not use is removed (language model runtime, web UI framework, ...).
# Both apps therefore run the very same PyTorch / sentence-transformers
# builds and compute the same vectors.  The result is about a fifth of the
# search image and needs no internet access to build.
#
#   docker build -t scopus-embedding:1.0 .
#   docker build --build-arg SEARCH_IMAGE=scopus-ai-gpu:1.1 -t scopus-embedding:1.0 .
ARG SEARCH_IMAGE=scopus-ai-gpu:1.1

FROM ${SEARCH_IMAGE} AS packages
COPY docker/prune_packages.py /tmp/prune_packages.py
RUN python /tmp/prune_packages.py \
    && find /opt/venv -name __pycache__ -prune -exec rm -rf {} + \
    && python -c "import torch, sentence_transformers, qdrant_client, fastapi, uvicorn, pymysql, rispy"

# PyTorch brings its own CUDA libraries; the NVIDIA driver comes from the host
# through the NVIDIA Container Toolkit.  Same Ubuntu (and Python 3.12) as the
# search image, so the copied virtual environment runs unchanged.
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    NVIDIA_VISIBLE_DEVICES=all \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility

RUN apt-get update && apt-get install -y --no-install-recommends python3 ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY --from=packages /opt/venv /opt/venv
COPY app /app/app
WORKDIR /app/app

EXPOSE 7861

CMD ["python", "-m", "embedding_service"]
