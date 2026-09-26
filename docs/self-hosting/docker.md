# Docker

`docker-compose.yml`, at the repository root, runs the full stack: database, cache/broker,
API, background workers and frontend, as six services from one file. This page documents that
file service by service. For the fastest path to a running instance with demo data loaded, see
[Docker quick start](../getting-started/docker-quick-start.md); this page instead covers what to
configure, how data is persisted, and how to upgrade an instance you intend to keep running.

**This compose file is a development setup, not a hardened production one.** Its `backend`
service bind-mounts the repository (`./backend:/app`) and its entrypoint
(`docker/backend/entrypoint.sh`) ends by running `python manage.py runserver 0.0.0.0:8000`, the
Django development server, not Gunicorn. `.env.docker` also ships `DEBUG=True`. Running it as-is
gets every service talking to each other correctly; it does not by itself satisfy the
requirements in [Production deployment](production-deploy.md).

## The services

`docker-compose.yml` defines:

| Service | Image / build | Host port | Purpose |
|---|---|---|---|
| `db` | `postgres:16-alpine` | `5432` | The PostgreSQL database, `crm_db`. Row-Level Security policies applied by Django migrations rely on this being real PostgreSQL. See [PostgreSQL and RLS](postgresql-and-rls.md). |
| `redis` | `redis:7-alpine` | `6379` | Celery broker and result backend (`CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` both point here). |
| `backend` | built from the repo-root `Dockerfile` | `8000` | The Django API, served via `manage.py runserver` in this compose file (see the warning above). |
| `celery-worker` | same image as `backend` |, (no published port) | Runs `celery -A crm worker --loglevel=info`; executes background tasks such as outbound email and RLS-context-aware jobs in `backend/common/tasks.py`. |
| `celery-beat` | same image as `backend` |, (no published port) | Runs `celery -A crm beat --loglevel=info`; fires the periodic tasks registered in `backend/crm/celery.py` (recurring invoices, overdue/expiry checks, SLA scanning, cleanup jobs) on their schedules. |
| `frontend` | built from `frontend/Dockerfile` | `5181` | The SvelteKit app, served via `pnpm dev --host 0.0.0.0`, also a dev server, not a production build. |

`backend`, `celery-worker` and `celery-beat` each declare `depends_on: db (service_healthy), redis
(service_healthy)`, using the `pg_isready` and `redis-cli ping` healthchecks defined on those two
services, so they wait for both to actually accept connections rather than just for their
containers to start.

## Configuration

Every service loads two env files, in order: `.env.docker` (checked into the repository, working
defaults) and then `.env.docker.local` (optional, gitignored, `required: false`, create it next
to `.env.docker` to override specific values without touching the tracked file; values there win).

The variables `.env.docker` sets: `SECRET_KEY` (an insecure placeholder. See
[Production deployment](production-deploy.md) before this instance is anything but local),
`DEBUG=True`, `ENV_TYPE=dev`, `ALLOWED_HOSTS=localhost,127.0.0.1,backend`,
`DOMAIN_NAME` (`http://localhost:8000`), the `DBNAME`/`DBUSER`/`DBPASSWORD`/`DBHOST`/
`DBPORT` the `backend` and Celery containers use to reach `db`, `POSTGRES_DB`/`POSTGRES_USER`/
`POSTGRES_PASSWORD` (used only by the `db` service itself, to create the database and its own
superuser role. See [PostgreSQL and RLS](postgresql-and-rls.md) for why the app never connects
as this role), `CELERY_BROKER_URL`/`CELERY_RESULT_BACKEND` (both pointing at `redis`),
`DEFAULT_FROM_EMAIL`/`ADMIN_EMAIL`/`ADMIN_PASSWORD`, `CORS_ALLOW_ALL=True`,
`GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` (blank. Google sign-in stays disabled until both
are set), and `PUBLIC_DJANGO_API_URL=http://backend:8000`, the compose service name the
frontend's server-side code uses to reach the API.

## Running it

From the repository root (the directory containing `docker-compose.yml` and `Dockerfile`):

```bash
docker compose up --build
```

This requires the Compose v2 plugin (the `docker compose` subcommand, not the standalone
`docker-compose` binary). On first boot, `docker/postgres/init-rls-user.sql` runs automatically
via PostgreSQL's `/docker-entrypoint-initdb.d/` mechanism and creates the non-superuser `crm_user`
role that `backend` actually connects as. `docker/backend/entrypoint.sh` then waits for
PostgreSQL to accept connections, runs `manage.py migrate --noinput`, runs
`manage.py create_default_admin`, runs `manage.py collectstatic --noinput`, and finally starts the
dev server. Every one of those steps runs again on every container restart, not just the first.

Run any one-off management command inside the running `backend` container with `docker compose
exec`, for example:

```bash
docker compose exec backend python manage.py migrate
docker compose exec backend python manage.py manage_rls --status
```

## Persisting data

`docker-compose.yml` declares two named volumes:

| Volume | Mounted at | Holds |
|---|---|---|
| `postgres_data` | `/var/lib/postgresql/data` on `db` | The actual database: every org, record, and RLS-protected row. This is the volume that must survive a rebuild. |
| `frontend_node_modules` | `/app/node_modules` on `frontend` | Installed frontend dependencies, a build cache, not user data. Safe to delete; `pnpm install` recreates it. |

Everything else is a bind mount of source code from the host, not a Docker-managed volume:
`./backend:/app` on `backend`/`celery-worker`/`celery-beat`, and `./frontend:/app` on `frontend`.
One consequence worth knowing: with `ENV_TYPE=dev` (the shipped default), Django's `MEDIA_ROOT` is
`BASE_DIR/media`, which resolves inside the container to `/app/media`, and because `/app` is the
bind-mounted `./backend` directory, uploaded files (invoice attachments, case attachments, and so
on) land in `backend/media/` on the host automatically. They persist across container rebuilds for
the same reason the source code does, without needing a dedicated named volume, but they are
**not** covered by a `postgres_data`-style volume, so back up `backend/media/` alongside the
database if you rely on uploaded files.

`docker compose down` removes containers and the default network but leaves named volumes intact;
only `docker compose down -v`, or removing `postgres_data`/`frontend_node_modules` explicitly,
deletes them.

## Upgrading

To move to a newer version of the application: pull the new source, then rebuild and restart:

```bash
docker compose up --build
```

Because `entrypoint.sh` runs `manage.py migrate --noinput` on every container start (not only the
first), rebuilding and restarting is sufficient to bring the schema up to date. There is no
separate migration step to remember. `collectstatic` also reruns on every start, so updated static
assets are picked up automatically.

Back up the `postgres_data` volume (and `backend/media/`, per the note above) before upgrading
across any change you're not certain is backward compatible with your existing data, nothing in
this compose setup takes an automatic backup for you.
