FROM python:3.12-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim

# Security hardening:
# - non-root user (UID 10001) so a compromise can't touch system files
# - PYTHONDONTWRITEBYTECODE avoids .pyc clutter in image
# - PYTHONUNBUFFERED flushes logs to stdout immediately for Fly/Docker log drivers
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends libpq5 && \
    rm -rf /var/lib/apt/lists/* && \
    useradd --system --uid 10001 --home /app --shell /usr/sbin/nologin cortex

COPY --from=builder /install /usr/local
COPY bot/ ./bot/
COPY alembic/ ./alembic/
COPY alembic.ini ./alembic.ini
COPY scripts/ ./scripts/

RUN chown -R cortex:cortex /app
USER cortex

CMD ["python", "-m", "bot"]
