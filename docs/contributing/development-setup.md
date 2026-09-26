# Development setup

This page gets a clone of the three BottleCRM projects (`backend/`, `frontend/`, `mobile/`) running
locally for development. For exact required versions (Python, PostgreSQL, Node, pnpm) see
[Requirements](../self-hosting/requirements.md); this page assumes you already have them installed.
For a from-scratch install walkthrough (including creating the database and `.env`), see
[Manual setup](../getting-started/manual-setup.md): this page focuses on the parts specific to
contributing: running the test suite, adding dependencies, and running the mobile app alongside the
other two.

## Backend

The backend is managed with [`uv`](https://docs.astral.sh/uv/), not pip or a manually-activated
virtualenv. `uv sync` reads `backend/pyproject.toml` and `backend/uv.lock` and creates
`backend/.venv/` with the exact locked dependency versions; every command after that runs through
`uv run <cmd>`, which resolves that venv automatically without you activating it.

```bash
cd backend
uv sync
cp .env.example .env
uv run python manage.py migrate
uv run python manage.py runserver
```

Edit `.env` first if your local PostgreSQL role, database name, or Redis URL differ from the
defaults. See [Manual setup](../getting-started/manual-setup.md) and
[Environment variables](../self-hosting/environment-variables.md).

Run the test suite from `backend/`. See [Testing](testing.md) for the fast-iteration flags and
what the markers mean:

```bash
uv run pytest
```

To add a dependency, use `uv add`, not a manual edit to `pyproject.toml`; `uv add` resolves the
version, writes it to `pyproject.toml`, and re-locks `uv.lock` in one step, so the two files never
drift out of sync:

```bash
uv add <package>                # runtime dependency, added to [project.dependencies]
uv add --group dev <package>    # dev-only dependency (tests, tooling), added to [dependency-groups]
```

To refresh every locked version against the current `pyproject.toml` constraints without adding or
removing a dependency (for example, to pick up a transitive security fix) use `uv lock --upgrade`
instead; it re-resolves and rewrites `uv.lock` in place.

`backend/pyproject.toml` currently declares two dependency groups: `dev` (`pytest`,
`pytest-django`, `pytest-cov`) and `docs` (`mkdocs-material`). `dev` is treated as a
default group by `uv`, so `uv sync` on its own installs it; a plain `uv sync --group docs` installs
`docs` **in addition to** `dev`, not instead of it: that's why CI's `docs-build` job, which runs
exactly `uv sync --frozen --group docs`, is able to run `uv run pytest ...` in the very next step
(see [Pull requests](pull-requests.md#what-ci-runs)). See [Code style](code-style.md) before reaching
for `uv add black` or similar. It is not as simple as it sounds in this repository.

## Frontend

The frontend uses [pnpm](https://pnpm.io/), pinned to major version 10 in CI
(`.github/workflows/tests.yml`).

```bash
cd frontend
pnpm install
pnpm run dev
```

`pnpm run dev` starts the Vite dev server on `http://localhost:5181`. `frontend/vite.config.js`
configures no dev-server proxy, there is no `server.proxy` entry in it, so the browser calls the
Django API directly, cross-origin: `frontend/src/lib/api.js` builds its base URL from
`PUBLIC_DJANGO_API_URL`, defaulting to `http://localhost:8000/api` when that variable is unset. That
means your backend's `CORS_ALLOWED_ORIGINS` has to include `http://localhost:5181` (it does by
default in `backend/.env.example`: see [Manual setup](../getting-started/manual-setup.md)) or every
request from the dev server fails as a CORS error, not a 404 or connection-refused, which is easy to
misdiagnose as a backend problem.

## Mobile

```bash
cd mobile
flutter pub get
flutter run
```

That gets the app compiling and running against whatever emulator or device is attached, but **it
will not talk to your local backend until you edit a compile-time constant first**. See
[Build and configure](../mobile/build-and-configure.md) and
[Connect to a self-hosted backend](../mobile/connect-to-self-hosted.md) before running it if you
intend to exercise real API calls rather than just confirm the app builds.

## Running everything

The fastest way to bring up the full stack: PostgreSQL, Redis, the Django API, a Celery worker, a
Celery beat scheduler, and the frontend dev server. Together is Docker Compose, from the repo
root:

```bash
docker compose up --build
```

See [Docker](../self-hosting/docker.md) for what each service does and how to reach them. Running
the pieces natively instead, for faster iteration on the backend or frontend, means starting
PostgreSQL and Redis yourself and then running each of these in its own terminal:

```bash
# Terminal 1: API
cd backend && uv run python manage.py runserver

# Terminal 2: background jobs (recurring invoices, SLA scans, notification emails, ...)
cd backend && uv run celery -A crm worker --loglevel=INFO

# Terminal 3: frontend
cd frontend && pnpm run dev
```

The Celery worker is optional for exercising most CRUD endpoints, but several features send email or
run scheduled work through it. See [Background jobs](../architecture/background-jobs.md) for what
runs where, and note that `celery -A crm beat` (the scheduler that actually fires the ten recurring
jobs described there) is a fourth, separate process that Docker Compose starts for you but the
native flow above does not.
