FROM python:3.12-slim-bookworm

# Prevent Python from buffering stdout/stderr (useful for Docker logs)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install system dependencies for WeasyPrint (cairo, pango) and PostgreSQL
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    libcairo2 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf2.0-0 \
    libffi-dev \
    shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Production containers must not run application code as root. Keep the UID
# stable so orchestrators can grant ownership to writable volumes explicitly.
RUN groupadd --system --gid 10001 crm \
    && useradd --system --uid 10001 --gid crm --home-dir /app --shell /usr/sbin/nologin crm

# Install uv (fast Python package manager).
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv

# Move the venv outside of backend so it doesn't get overwritten with the copy
ENV UV_PROJECT_ENVIRONMENT=/opt/venv

# Install Python dependencies into /app/.venv (layer cached on lockfile changes)
COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./
RUN uv sync --frozen --no-install-project

# Copy backend source
COPY --chown=crm:crm backend/ .

# The API contract tests intentionally compare the generated OpenAPI schema
# with the curated endpoint reference. Keep that reference in the image so the
# guard cannot pass or fail merely because the Docker build omitted its input.
COPY docs/api/ /docs/api/

RUN mkdir -p /app/staticfiles /app/media \
    && chown -R crm:crm /app /docs/api

# Put the venv's binaries on PATH so `python`, `gunicorn`, `celery` etc. resolve.
ENV PATH="/opt/venv/bin:$PATH"

EXPOSE 8000

USER 10001:10001
