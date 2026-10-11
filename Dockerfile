FROM nvidia/cuda:12.6.3-devel-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    CMAKE_ARGS="-DGGML_CUDA=on" \
    FORCE_CMAKE=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-dev python3-venv \
    build-essential cmake ninja-build git \
    libssl-dev libffi-dev ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Ubuntu 24.04 manages the system pip via apt/PEP 668. Use an isolated venv
# so pip can be upgraded and Python packages can be installed safely.
RUN python3 -m venv "$VIRTUAL_ENV" && \
    "$VIRTUAL_ENV/bin/python" -m pip install --upgrade pip setuptools wheel

COPY requirements.txt /app/requirements.txt

# Install project dependencies in the venv. The llama-index llama-cpp package
# may pull a CPU llama-cpp-python first; the next layer deliberately replaces
# it with a CUDA-enabled build.
RUN python -m pip install --no-cache-dir -r /app/requirements.txt

# Rebuild llama-cpp-python with CUDA support.
RUN python -m pip uninstall -y llama-cpp-python || true && \
    CMAKE_ARGS="-DGGML_CUDA=on" FORCE_CMAKE=1 \
    python -m pip install --no-cache-dir --upgrade --force-reinstall llama-cpp-python

COPY app /app/app
WORKDIR /app/app

EXPOSE 7860 7861

CMD ["python", "main.py"]
