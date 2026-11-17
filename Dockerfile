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

# Install uv (fast Python package manager).
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv

# Move the venv outside of backend so it doesn't get overwritten with the copy
ENV UV_PROJECT_ENVIRONMENT=/opt/venv

# Install Python dependencies into /app/.venv (layer cached on lockfile changes)
COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./
RUN uv sync --frozen --no-install-project

# Copy backend source
COPY backend/ .

# Install the Render production startup wrapper. It applies migrations and
# collects static files before handing control to the configured app command.
COPY docker/backend/render-entrypoint.sh /usr/local/bin/render-entrypoint
RUN chmod 0755 /usr/local/bin/render-entrypoint

# Put the venv's binaries on PATH so `python`, `gunicorn`, `celery` etc. resolve.
ENV PATH="/opt/venv/bin:$PATH"

EXPOSE 8000

ENTRYPOINT ["/usr/local/bin/render-entrypoint"]
