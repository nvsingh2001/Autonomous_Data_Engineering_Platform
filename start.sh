#!/bin/bash
set -e

MOUNT="${ADEP_MOUNT:-/mnt/adep}"

mkdir -p "${MOUNT}/data" "${MOUNT}/reports" "${MOUNT}/.chroma"

[ ! -L /app/data ] && ln -sfn "${MOUNT}/data" /app/data
[ ! -L /app/reports ] && ln -sfn "${MOUNT}/reports" /app/reports
[ ! -L /app/.chroma ] && ln -sfn "${MOUNT}/.chroma" /app/.chroma

echo "[start] Disk symlinks ready"

exec uvicorn app.server:app \
  --host 0.0.0.0 \
  --port "${PORT:-8000}" \
  --workers 1 \
  --loop uvloop \
  --timeout-keep-alive 125
