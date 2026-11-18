#!/bin/sh
# Render production startup: wait for PostgreSQL, apply schema changes, prepare
# static assets, then hand off to the service command configured in Render.
set -eu

if [ "$#" -eq 0 ]; then
    echo "ERROR: No process command was provided to the container."
    exit 64
fi

: "${DBHOST:?DBHOST must be configured}"
: "${DBPORT:?DBPORT must be configured}"

echo "Waiting for PostgreSQL at ${DBHOST}:${DBPORT}..."
python - <<'PY'
import os
import socket
import sys
import time

host = os.environ["DBHOST"]
port = int(os.environ["DBPORT"])
attempts = 60

for attempt in range(1, attempts + 1):
    try:
        with socket.create_connection((host, port), timeout=3):
            print("PostgreSQL is reachable.")
            break
    except OSError as exc:
        if attempt == attempts:
            print(
                f"ERROR: PostgreSQL did not become reachable after {attempts} attempts: {exc}",
                file=sys.stderr,
            )
            sys.exit(1)
        print(f"PostgreSQL not ready (attempt {attempt}/{attempts}); retrying...")
        time.sleep(2)
PY

if [ "${SKIP_DJANGO_STARTUP_TASKS:-false}" = "true" ]; then
    echo "Skipping migrations and collectstatic for this non-web process."
else
    echo "Applying Django database migrations..."
    python manage.py migrate --noinput

    echo "Collecting Django static files..."
    python manage.py collectstatic --noinput
fi

echo "Starting application process: $*"
exec "$@"
