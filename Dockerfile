# syntax=docker/dockerfile:1.7
FROM python:3.12-slim-bookworm AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_COMPILE_BYTECODE=1

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install uv from official binary
COPY --from=ghcr.io/astral-sh/uv:0.6.2 /uv /bin/uv

COPY pyproject.toml requirements.txt ./
RUN for i in 1 2 3; do uv pip install --system --no-cache -r requirements.txt && break || ([ $i -lt 3 ] && sleep 10); done

# Final runtime image
FROM python:3.12-slim-bookworm AS final

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DJANGO_SETTINGS_MODULE=backend.settings

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy installed site-packages from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy backend code
COPY backend/ /app/

# Create unprivileged system user for HIPAA/PCI security compliance
RUN groupadd -r amrutam && useradd -r -g amrutam -d /app -s /sbin/nologin amrutam \
    && chown -R amrutam:amrutam /app

USER amrutam

EXPOSE 8000

CMD ["gunicorn", "backend.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "4", "--threads", "2", "--timeout", "60"]
