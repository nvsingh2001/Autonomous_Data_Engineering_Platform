FROM python:3.12-slim

# libgomp1: required by onnxruntime (chromadb's embedding backend)
# curl: used by Render health check probing
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies first — this layer is cached unless requirements.in changes,
# so source-only edits don't re-download 1.3 GB of packages.
COPY requirements.in .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.in

# Copy read-only application source.
# data/, reports/, .chroma/ are intentionally NOT copied —
# start.sh symlinks them from the Render persistent disk at runtime.
COPY agents/   ./agents/
COPY app/      ./app/
COPY config/   ./config/
COPY pipeline/ ./pipeline/
COPY tasks/    ./tasks/
COPY tools/    ./tools/
COPY crew.py   .
COPY main.py   .
COPY start.sh  .

RUN chmod +x start.sh

EXPOSE 8000

CMD ["./start.sh"]
