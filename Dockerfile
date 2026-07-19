FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
  libgomp1 \
  curl \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
  && pip install --no-cache-dir --no-deps -r requirements.txt

COPY agents/   ./agents/
COPY app/      ./app/
COPY config/   ./config/
COPY pipeline/ ./pipeline/
COPY schemas/  ./schemas/
COPY skills/   ./skills/
COPY tasks/    ./tasks/
COPY tools/    ./tools/
COPY utils/    ./utils/
COPY config.py .
COPY logging_setup.py .
COPY crew.py   .
COPY main.py   .
COPY start.sh  .

RUN chmod +x start.sh

# /mnt/adep pre-owned by the user so named volumes mount writable
RUN useradd --create-home --uid 1000 appuser \
  && mkdir -p /mnt/adep \
  && chown -R appuser:appuser /app /mnt/adep
USER appuser

EXPOSE 8000

CMD ["./start.sh"]
