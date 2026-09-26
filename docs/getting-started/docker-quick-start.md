# Docker quick start

The fastest way to run BottleCRM locally is Docker Compose. It brings up PostgreSQL, Redis, the
Django API, two Celery processes and the SvelteKit frontend from one file, using the checked-in
`.env.docker` for configuration.

## Prerequisites

- Docker Engine with the Compose v2 plugin (the `docker compose` subcommand, not the standalone
  `docker-compose` binary: `docker-compose.yml` is written for the plugin).
- A clone of the repository, with `docker-compose.yml` at its root.

## Clone and configure

All commands below run from the repository root, the directory that contains
`docker-compose.yml` and `Dockerfile`.

`.env.docker` is checked into the repository with working development defaults and is loaded by
every service in `docker-compose.yml`. You don't need to copy or edit anything to get a working
stack. If you do want to override a value locally (for example to add Google OAuth credentials),
create `.env.docker.local` next to it, `docker-compose.yml` loads it as an optional, gitignored
second `env_file` whose values win over `.env.docker`.

The variables `.env.docker` actually defines are:

| Variable | Purpose |
|---|---|
| `SECRET_KEY` | Django secret key. The shipped value is an insecure dev placeholder. |
| `DEBUG` | `True` in the dev compose file. This is also what allows `devlogin` to run (see [First sign-in](first-sign-in.md)). |
| `ENV_TYPE` | `dev`. |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1,backend`. |
| `DOMAIN_NAME` | `http://localhost:8000`. |
| `DBNAME`, `DBUSER`, `DBPASSWORD`, `DBHOST`, `DBPORT` | Connection details the Django `backend` (and Celery) containers use to reach `db`. |
| `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` | Used only by the `db` service itself to create the database and its own superuser role. |
| `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND` | Both point at the `redis` service, database `0`. |
| `DEFAULT_FROM_EMAIL`, `ADMIN_EMAIL`, `ADMIN_PASSWORD` | Console email backend (dev), and the credentials used to bootstrap a Django admin superuser. See [What you got](#what-you-got). |
| `CORS_ALLOW_ALL` | `True` for local development. |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Blank by default; Google sign-in is disabled until both are set. The redirect URI is not a setting: the frontend sends it in the request body on each call. |
| `PUBLIC_DJANGO_API_URL` | The API origin the frontend's server-side code calls, `http://backend:8000`. It uses the compose service name because `localhost` inside the frontend container is the container itself. Your browser still reaches the API at `http://localhost:8000`. |

## Start the stack

```bash
docker compose up --build
```

This builds and starts six services, defined in `docker-compose.yml`:

| Service | Image / build | Host port |
|---|---|---|
| `db` | `postgres:16-alpine` | `5432` |
| `redis` | `redis:7-alpine` | `6379` |
| `backend` | built from the repo root `Dockerfile` | `8000` |
| `celery-worker` | same image as `backend`, runs `celery -A crm worker --loglevel=info` |. |
| `celery-beat` | same image as `backend`, runs `celery -A crm beat --loglevel=info` |, |
| `frontend` | built from `frontend/Dockerfile` | `5181` |

`backend`, `celery-worker` and `celery-beat` all wait for `db` and `redis` to report healthy
before starting. The `backend` container's entrypoint (`docker/backend/entrypoint.sh`) waits for
PostgreSQL to accept connections, runs `migrate`, runs `create_default_admin`, collects static
files, and then starts the Django dev server on `0.0.0.0:8000`.

**The database role matters.** `db` starts as the `postgres` superuser (from `POSTGRES_USER`).
That account exists only so the container can create the `crm_db` database. On first boot,
`docker/postgres/init-rls-user.sql` runs automatically (via Postgres's
`/docker-entrypoint-initdb.d/` mechanism) and creates a second, non-superuser role, `crm_user`,
granting it the privileges it needs on `crm_db`. The Django containers connect as `crm_user`
(`DBUSER` in `.env.docker`), never as `postgres`. This is required, not incidental: PostgreSQL
Row-Level Security is bypassed entirely for a superuser connection, so if the app ever connected
as `postgres`, every RLS policy in the schema would be silently inert and tenant isolation would
depend on application-level filtering alone.

## Migrate and seed

`entrypoint.sh` already runs `migrate` on every container start, so the schema is up to date as
soon as `backend` reports it's serving. To load demo data, run the seeder inside the running
`backend` container:

```bash
docker compose exec backend python manage.py seed_data --email you@example.com
```

`seed_data` creates an organization named **`MicroPyramid`** (the first organization it creates
is always named this, specifically so local-dev workflows have a known, stable name to sign in
against) and an **admin profile** for the user you passed with `--email`, along with a full set
of demo leads, accounts, contacts, opportunities, cases, tasks, invoices and more. Re-running the
command reuses the existing `MicroPyramid` org and admin user rather than creating duplicates.

You can also run migrations manually, the same way you'd run any other management command against
the container:

```bash
docker compose exec backend python manage.py migrate
```

## Sign in

With `DEBUG=True` (the default in `.env.docker`), the fastest way in is `devlogin`, a
management command that mints a JWT pair directly, without going through Google OAuth or email.
Full details, including how it resolves `--org` and what it requires of the target user, are in
[First sign-in](first-sign-in.md). For the org and user you just seeded:

```bash
docker compose exec backend python manage.py devlogin you@example.com --org MicroPyramid
```

This prints an access token, a refresh token, and the `MicroPyramid` org's UUID, already bound
into the token. See [First sign-in](first-sign-in.md) for how to use them from the frontend at
`http://localhost:5181`.

## What you got

After `docker compose up --build` and one `seed_data` run, you have:

- A PostgreSQL 16 database (`crm_db`) with RLS-enforcing policies applied by the migrations.
- A Django API on `http://localhost:8000`, running under the dev server (not a production WSGI
  server. This compose file is for development).
- A Celery worker and a Celery beat scheduler, both connected to the `redis` service, running the
  periodic and background tasks registered in `backend/crm/celery.py`.
- A SvelteKit frontend on `http://localhost:5181`, which reaches the API over the compose network at `PUBLIC_DJANGO_API_URL=http://backend:8000`.
- A Django admin superuser, created automatically by `create_default_admin` on first boot, using
  `ADMIN_EMAIL` / `ADMIN_PASSWORD` from `.env.docker` (`admin@localhost` / `admin` unless you
  override them). This account can sign in to Django's own `/admin/` site; it does not have a CRM
  organization profile, so it can't sign in to the CRM app itself unless you also give it a
  profile via `seed_data` or the org-creation flow.
- If you ran `seed_data`, an org named `MicroPyramid` with an admin profile for the email you
  passed, plus a large set of demo records.

## Next steps

- [First sign-in](first-sign-in.md). The three ways to get a session, including the exact
  `devlogin` invocation and what Google OAuth and magic links need.
- [Demo data and packs](demo-data.md), what `seed_data` creates in detail, and how vertical packs
  layer industry-specific pipelines and sample records on top of it.
- If Docker isn't the right fit, [Manual setup](manual-setup.md) covers running the backend,
  database, Redis, Celery and frontend directly on your machine.
