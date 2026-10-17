# Images of the search app, built from public base images only (no local image needed):
#
#   ./scripts/get_llama_server_source.sh   llama.cpp source archive -> third_party/ (once)
#   docker compose build                   both images (./scripts/build_online.sh does both steps)
#
#   target llm  scopus-llm:b10991    language model server: llama.cpp llama-server compiled for CUDA
#   target app  scopus-ai-gpu:1.1    search UI: Python, PyTorch (with its own CUDA libraries)
#
# The embedding app's image is made from the app image (../embedding/Dockerfile): build
# this one first.  The NVIDIA driver comes from the host (NVIDIA Container Toolkit).

ARG CUDA_IMAGE=12.6.3
ARG LLAMA_CPP_VERSION=b10991

# ---------------------------------------------------------------- language model server

FROM nvidia/cuda:${CUDA_IMAGE}-devel-ubuntu24.04 AS llm-build
ARG LLAMA_CPP_VERSION
# CUDA kernels: compute 7.5 (RTX 20xx / GTX 16xx), 8.6 (RTX 30xx, A-series), 8.9 (RTX 40xx,
# L4/L40) and PTX that newer GPUs compile at first start.  Add e.g. 80-real (A100) or
# 90-real (H100).  GGML_NATIVE=OFF: generic x86-64 build, not tuned to the build PC's CPU.
ARG CUDA_ARCHITECTURES="75-real;86-real;89-real;89-virtual"
RUN apt-get update && apt-get install -y --no-install-recommends build-essential cmake ninja-build git \
    && rm -rf /var/lib/apt/lists/*
# The source archive is copied in (no network during the build; see get_llama_server_source.sh).
COPY third_party/llama.cpp-${LLAMA_CPP_VERSION}.tar.gz /tmp/
RUN tar xzf /tmp/llama.cpp-${LLAMA_CPP_VERSION}.tar.gz -C /tmp \
 && cmake -S /tmp/llama.cpp-${LLAMA_CPP_VERSION} -B /tmp/build -G Ninja \
      -DCMAKE_BUILD_TYPE=Release \
      -DGGML_CUDA=ON -DGGML_NATIVE=OFF \
      -DCMAKE_CUDA_ARCHITECTURES="${CUDA_ARCHITECTURES}" \
      -DBUILD_SHARED_LIBS=OFF \
      -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF -DLLAMA_BUILD_SERVER=ON \
      -DLLAMA_BUILD_UI=OFF -DLLAMA_USE_PREBUILT_UI=OFF -DLLAMA_OPENSSL=OFF \
      -DCMAKE_EXE_LINKER_FLAGS=-Wl,--allow-shlib-undefined \
 && cmake --build /tmp/build --target llama-server -j"$(nproc)"

# The server runs on the CUDA runtime image (CUDA libraries without the compilers).
FROM nvidia/cuda:${CUDA_IMAGE}-runtime-ubuntu24.04 AS llm
# python: the health check in docker-compose.yml; libgomp1: OpenMP of llama.cpp's CPU code.
RUN apt-get update && apt-get install -y --no-install-recommends python3 python-is-python3 libgomp1 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=llm-build /tmp/build/bin/llama-server /usr/local/bin/llama-server
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/llama-server"]
CMD ["--help"]

# ---------------------------------------------------------------- search UI

FROM ubuntu:24.04 AS app
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    NVIDIA_VISIBLE_DEVICES=all \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=60 \
    PIP_RETRIES=20 \
    PIP_RESUME_RETRIES=50
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*
# Ubuntu 24.04 manages the system Python (PEP 668): the packages go into a venv.
RUN python3 -m venv "$VIRTUAL_ENV" && python -m pip install --no-cache-dir --upgrade pip
COPY requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir -r /app/requirements.txt
COPY app /app/app
WORKDIR /app/app
EXPOSE 7860
CMD ["python", "-m", "search_service"]
