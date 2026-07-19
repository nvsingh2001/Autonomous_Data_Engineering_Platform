#!/bin/bash
set -e

MOUNT="${ADEP_MOUNT:-/mnt/adep}"

mkdir -p "${MOUNT}/data" "${MOUNT}/reports" "${MOUNT}/.chroma"

[ ! -L /app/data ] && ln -sfn "${MOUNT}/data" /app/data
[ ! -L /app/reports ] && ln -sfn "${MOUNT}/reports" /app/reports
[ ! -L /app/.chroma ] && ln -sfn "${MOUNT}/.chroma" /app/.chroma

echo "[start] Disk symlinks ready"

# Same-container topology (Railway volumes attach to one service): the two
# Celery workers share the web container's disk. Prefork -c 1 on the pipeline
# queue preserves one-run-at-a-time; prefork (not solo) so time limits work.
celery -A app.celery_app worker -Q pipeline -c 1 -n pipeline@%h \
  --loglevel="${CELERY_LOGLEVEL:-INFO}" &
celery -A app.celery_app worker -Q chat -c "${CHAT_CONCURRENCY:-2}" -n chat@%h \
  --loglevel="${CELERY_LOGLEVEL:-INFO}" &

echo "[start] Celery workers launched (pipeline c=1, chat c=${CHAT_CONCURRENCY:-2})"

uvicorn app.server:app \
  --host 0.0.0.0 \
  --port "${PORT:-8000}" \
  --workers 1 \
  --loop uvloop \
  --timeout-keep-alive 125 &

trap 'kill -TERM $(jobs -p) 2>/dev/null' TERM INT

# If any process dies, exit so the platform restarts the whole container —
# a dead worker must not leave a "healthy" web tier queueing runs forever.
wait -n
code=$?
echo "[start] a process exited (code $code) — shutting down container" >&2
kill $(jobs -p) 2>/dev/null
exit "$code"
