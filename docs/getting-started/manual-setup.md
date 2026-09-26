# Manual setup

Running the backend, database, Redis, Celery and frontend directly on your machine, without
Docker. Useful if you want faster iteration on backend code than a bind-mounted container gives
you, or if you're setting up on a host where Docker isn't available.

## Prerequisites

- **Python 3.12 or later**: `backend/pyproject.toml` declares `requires-python = ">=3.12"`, and
  `backend/.python-version` pins `3.12`.
- **[uv](https://docs.astral.sh/uv/)**: the backend is managed with uv, not pip or a manually
  created virtualenv. `uv sync` reads `backend/pyproject.toml` and `backend/uv.lock` and creates
  a `.venv/` for you; every backend command in this guide runs via `uv run <cmd>`, which resolves
  that venv automatically. Install it once, system-wide:

  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  # or, on macOS: brew install uv
  ```
- **PostgreSQL**: the only supported database backend (`django.db.backends.postgresql` in
  `backend/crm/settings.py`); Row-Level Security depends on it.
- **Redis**: used as the Celery broker and result backend.
- **Node.js and [pnpm](https://pnpm.io/)**: for the SvelteKit frontend in `frontend/`.

## Backend

From `backend/`:

```bash
cd backend
uv sync
```

This creates `.venv/` and installs everything listed in `pyproject.toml`'s `dependencies`
(Django, DRF, Celery, `djangorestframework-simplejwt`, `weasyprint`, etc.) at the versions locked
in `uv.lock`.

Copy the example environment file and adjust it for your local database and Redis:

```bash
cp .env.example .env
```

`backend/crm/settings.py` loads this file via `python-dotenv` (`load_dotenv()` at import time), so
anything you put in `.env` is picked up the next time you run a management command. At minimum,
review `DBNAME`, `DBUSER`, `DBPASSWORD`, `DBHOST`, `DBPORT` against the database role you create in
the next section.

Once `.env` points at a reachable database, apply migrations:

```bash
uv run python manage.py migrate
```

And run the dev server:

```bash
uv run python manage.py runserver
```

By default this serves on `http://127.0.0.1:8000`.

## Database

The Django `DATABASES` setting (`backend/crm/settings.py`) reads `DBNAME`, `DBUSER`,
`DBPASSWORD`, `DBHOST` and `DBPORT` from the environment, defaulting to a local `postgres`
superuser connection if they're unset. Don't leave it on that default: **the role Django connects
as must not be a PostgreSQL superuser**, or Row-Level Security is bypassed for every query that
role makes, regardless of what the RLS policies say.

Create a dedicated, non-superuser role and database, for example:

```bash
sudo -u postgres psql
```

```sql
CREATE DATABASE crm_db;
CREATE USER crm_user WITH PASSWORD 'crm_password';
GRANT ALL PRIVILEGES ON DATABASE crm_db TO crm_user;
\connect crm_db;
GRANT ALL ON SCHEMA public TO crm_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO crm_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO crm_user;
```

Then point `.env` at it (`DBNAME=crm_db`, `DBUSER=crm_user`, `DBPASSWORD=crm_password`,
`DBHOST=localhost`, `DBPORT=5432`) and run `uv run python manage.py migrate`.

You can confirm the role is configured correctly with the RLS management command:

```bash
uv run python manage.py manage_rls --status
uv run python manage.py manage_rls --verify-user
```

## Redis and Celery

Redis needs to be reachable at the URL in `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` (both
default to `redis://localhost:6379/0` in `.env.example`). Install it locally through your package
manager, or run the same image the Docker setup uses:

```bash
docker run -p 6379:6379 redis:7-alpine
```

With Redis reachable, start a Celery worker from `backend/`:

```bash
uv run celery -A crm worker --loglevel=INFO
```

`backend/crm/celery.py` also defines a set of periodic tasks (recurring-invoice generation,
overdue-invoice and expired-estimate checks, stale-opportunity and goal-milestone checks, SLA
breach scanning, notification and refresh-token cleanup) on cron-style schedules. To run those on
schedule you also need Celery beat, in a separate terminal:

```bash
uv run celery -A crm beat --loglevel=INFO
```

Neither process is required for the API itself to respond to requests. They only matter for
background and scheduled work.

## Frontend

From `frontend/`:

```bash
cd frontend
pnpm install
```

Copy the example environment file:

```bash
cp .env.example .env
```

At minimum, `PUBLIC_DJANGO_API_URL` should point at your backend (`http://localhost:8000` if
you're following the ports in this guide). Then start the dev server:

```bash
pnpm run dev
```

This serves the SvelteKit app on `http://localhost:5181` (`vite dev` under the hood, per the
`dev` script in `frontend/package.json`).

## Running all three

A full local stack is four long-running processes across (at least) three terminals, all started
from the state above:

```bash
# Terminal 1: from backend/
uv run python manage.py runserver

# Terminal 2: from backend/
uv run celery -A crm worker --loglevel=INFO

# Terminal 3: from frontend/
pnpm run dev
```

Redis and PostgreSQL need to already be running before any of these start (either installed
locally or run as standalone containers, as shown above). Add a fourth terminal for
`uv run celery -A crm beat --loglevel=INFO` from `backend/` if you need scheduled tasks to fire.

Once all of this is up, see [First sign-in](first-sign-in.md) for how to get an authenticated
session, and [Demo data and packs](demo-data.md) for loading sample data.
