# BottleCRM Backend - Django REST API

BottleCRM is a self-hosted CRM you run on your own infrastructure, MIT licensed and
free to use. This package is the backend: a Django REST Framework API that serves
both the SvelteKit web app and the Flutter mobile client from one set of endpoints.

```bash
pip install django-crm
```

**What ships in it:** leads, contacts, customer accounts, a sales pipeline with deal
tracking, tasks, support tickets with solutions, approvals and escalation, invoices,
estimates, recurring invoices, products, orders, business hours and holiday calendars
for SLA timing, and saved-reply macros. Every app is listed below.

**Multi-tenancy is enforced in the database, not just the ORM.** Tenant isolation
uses PostgreSQL row-level security keyed on the organization claim in the JWT, so a
missing filter in application code cannot leak another tenant's rows. Setup and the
non-negotiable rules are in [RLS_SETUP.md](https://github.com/Django-CRM/Django-CRM/blob/master/RLS_SETUP.md).

Source, issues and releases: <https://github.com/Django-CRM/Django-CRM>

## Tech Stack

Minimum versions, as declared in `pyproject.toml`. That file is the source of
truth; the list here is a summary and `uv.lock` pins what actually gets
installed.

- **Django 6.0.7+** - Web framework
- **Django REST Framework 3.17+** - API toolkit
- **PostgreSQL** - Database, via **psycopg 3.2.10+** with the `pool` extra
- **Celery 5.6+** - Async task queue
- **Redis 8.0+** - Message broker for Celery
- **djangorestframework-simplejwt 5.5+** - JWT authentication
- **drf-spectacular 0.30+** - OpenAPI/Swagger documentation
- **django-ses 4.7+** - AWS SES email backend
- **WeasyPrint 69+** - Invoice and estimate PDF generation
- **Sentry SDK 2.66+** - Error tracking

> psycopg 3, not psycopg2. The `pool` extra is required rather than optional:
> Django raises `ImproperlyConfigured` for pool options under psycopg2, and
> `DATABASES["default"]["OPTIONS"]["pool"]` depends on it.

## Django Apps

| App | Description |
|-----|-------------|
| `common` | User, Organization, Profile, Teams, Comments, Attachments, Document models |
| `accounts` | Customer account management |
| `leads` | Lead tracking and conversion |
| `contacts` | Contact management |
| `opportunity` | Sales pipeline and deal tracking |
| `cases` | Customer support tickets, solutions, approvals, escalation |
| `tasks` | Task management |
| `invoices` | Invoices, estimates, recurring invoices, products |
| `orders` | Orders and order line items |
| `business_hours` | Business hours and holiday calendars for SLA timing |
| `macros` | Saved reply and action macros for cases |

`teams` was merged into `common`. The `emails`, `events`, `planner` and
`boards` apps were removed after 0.9.0; see the release notes if you are
upgrading from that version.

## Prerequisites

- **Python 3.12+** (uv installs a matching Python automatically if needed)
- **PostgreSQL**
- **Redis** (for Celery)
- **[uv](https://docs.astral.sh/uv/)**: Python package & venv manager (replaces pip + virtualenv)

## Installation

### 1. Install uv

```bash
# Linux / macOS
curl -LsSf https://astral.sh/uv/install.sh | sh

# Or via Homebrew
brew install uv
```

### 2. Install Python dependencies

`uv sync` reads `pyproject.toml` + `uv.lock`, picks the Python version from `.python-version`, and creates `.venv/` with everything installed.

```bash
cd backend
uv sync
```

> Run any backend command with `uv run <cmd>` (e.g. `uv run python manage.py migrate`). uv resolves binaries from `.venv/bin/` automatically, no manual `source .venv/bin/activate` needed (though that still works if you prefer it).

### 3. Install PDF generation system dependencies

Invoice PDF generation uses WeasyPrint (a runtime dep). It requires system libraries that must be installed separately:

**Ubuntu/Debian:**
```bash
sudo apt-get install -y \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libcairo2 \
    libgdk-pixbuf2.0-0 \
    libffi-dev \
    shared-mime-info
```

**macOS:**
```bash
brew install pango cairo libffi gdk-pixbuf
```

**Fedora/CentOS:**
```bash
sudo dnf install -y \
    pango \
    cairo \
    gdk-pixbuf2 \
    libffi-devel
```

**Windows:**
Follow the [WeasyPrint Windows installation guide](https://doc.courtbouillon.org/weasyprint/stable/first_steps.html#windows).

> **Note**: If you skip this step, the CRM will work but PDF download for invoices will show "PDF generation unavailable".

### 4. Configure environment variables

Create a `.env` file in the `backend/` directory:

```env
# Django
# At least 32 bytes: this key also signs the JWTs, and HS256 needs one that long.
# python -c "import secrets; print(secrets.token_urlsafe(48))"
SECRET_KEY=your-secret-key-here
ENV_TYPE=dev
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1

# Database
DBNAME=bottlecrm
DBUSER=postgres
DBPASSWORD=root
DBHOST=localhost
DBPORT=5432

# Email
DEFAULT_FROM_EMAIL=noreply@bottlecrm.com
ADMIN_EMAIL=admin@bottlecrm.com

# Celery
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/0

# The web app, NOT this API. Every link the backend emails is built from it:
# the magic-link sign-in URL, the customer invoice and estimate portals, the
# CSAT survey. Point it at the API host and every one of those links 404s.
FRONTEND_URL=http://localhost:5173

# Google sign-in. Without these the OAuth login flow cannot complete, which is
# the only interactive way into the app.
GOOGLE_CLIENT_ID=your-google-client-id
GOOGLE_CLIENT_SECRET=your-google-client-secret

# Where the browser calls this API from. The SvelteKit dev server by default.
CORS_ALLOWED_ORIGINS=http://localhost:5173

# This API's own public origin
DOMAIN_NAME=http://localhost:8000
```

### 5. Set up database

```bash
# Create PostgreSQL database
sudo -u postgres psql
CREATE DATABASE bottlecrm WITH OWNER = postgres;
ALTER USER postgres WITH PASSWORD 'root';
\q

# Run migrations
uv run python manage.py migrate

# Create superuser (optional)
uv run python manage.py createsuperuser
```

### 6. Run the development server

```bash
uv run python manage.py runserver
```

The API will be available at `http://localhost:8000`

## Running Celery

For background tasks (emails, notifications), run the Celery worker:

```bash
uv run celery -A crm worker --loglevel=INFO
```

## API Documentation

- **Swagger UI**: http://localhost:8000/swagger-ui/
- **ReDoc**: http://localhost:8000/api/schema/redoc/
- **Django Admin**: http://localhost:8000/admin/

### Generating Schema

To generate the OpenAPI schema file:

```bash
uv run python manage.py spectacular --file openapi.yml
```

## Architecture

### Multi-Tenancy

Every request operates within an organization context:

- **Organization (Org)**: Top-level tenant container
- **Users**: Regular members with USER role
- **Admins**: Organization administrators with ADMIN role
- **Super Admin**: Users with `is_superuser` set on the user record have platform-wide access. Grant it deliberately (`manage.py createsuperuser` or the Django admin). It is never inferred from the email address

### Authentication

JWT-based authentication:

```
Authorization: Bearer <token>
```

- Organization ID is embedded in the JWT token (not sent as header)
- Access token lifetime: 1 hour
- Refresh token lifetime: 14 days
- Refresh tokens are single-use: `/api/auth/refresh-token/` blacklists the token you send and returns a replacement, so clients must persist the new `refresh` value from every response

### Middleware

The middleware chain provides security:

1. **`GetProfileAndOrg`** (`common.middleware.get_company`):
   - Extracts org_id from JWT token claims (not headers - prevents spoofing)
   - Validates user has active membership in the organization
   - Sets `request.profile` and `request.org`

2. **`RequireOrgContext`** (`common.middleware.rls_context`):
   - Sets PostgreSQL session variable `app.current_org` for RLS
   - Resets context after each request

### Row-Level Security (RLS)

PostgreSQL RLS provides database-level tenant isolation as defense-in-depth.

#### How It Works

1. **Middleware sets context**: `SET app.current_org = '<org_id>'`
2. **RLS policies filter queries**: Only rows matching `org_id` are visible
3. **Fail-safe design**: Empty context returns zero rows (NULLIF pattern)

#### Protected Tables

`ORG_SCOPED_TABLES` in `common/rls/__init__.py` is the list, and the only one
worth trusting. It is deliberately not reproduced here: this section used to
carry a copy and a count, and both went stale, naming tables that no longer
exist and a total that was wrong by more than double.

```bash
uv run python manage.py manage_rls --status   # what is actually protected
```

#### Configuration

RLS is configured in `common/rls/__init__.py`:

```python
from common.rls import RLS_CONFIG, get_enable_policy_sql

# List of protected tables
tables = RLS_CONFIG["tables"]

# Enable RLS on a table
cursor.execute(get_enable_policy_sql("my_table"))
```

#### Management Commands

```bash
# Check RLS status on all tables
uv run python manage.py manage_rls --status

# Verify database user is non-superuser (required for RLS)
uv run python manage.py manage_rls --verify-user

# Test RLS isolation between organizations
uv run python manage.py manage_rls --test
```

Policies are enabled by migration, using `get_enable_policy_sql()`, so there is
no `--enable` flag to run by hand and no `--disable` to reach for when
something is in the way. A table becomes protected when its migration says so.

#### Critical: Database User Setup

**PostgreSQL superusers bypass ALL RLS policies.** You must use a non-superuser:

```sql
-- Create application user
CREATE USER crm_app WITH PASSWORD 'your_secure_password';

-- Grant permissions
GRANT CONNECT ON DATABASE bottlecrm TO crm_app;
GRANT USAGE ON SCHEMA public TO crm_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO crm_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO crm_app;

-- Future tables inherit permissions
ALTER DEFAULT PRIVILEGES IN SCHEMA public
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO crm_app;
```

Update `.env`:
```env
DBUSER=crm_app
DBPASSWORD=your_secure_password
```

#### Celery Tasks & RLS

Background tasks don't go through middleware, so set RLS context manually:

```python
from common.tasks import set_rls_context


@app.task
def my_background_task(data_id, org_id):
    set_rls_context(org_id)  # Required!
    obj = MyModel.objects.get(id=data_id)
    # ... process
```

#### Adding RLS to New Tables

1. Add table name to `ORG_SCOPED_TABLES` in `common/rls/__init__.py`
2. Create migration using `get_enable_policy_sql()`
3. Ensure model has `org = models.ForeignKey(Org, ...)`

### BaseModel Pattern

All models inherit from `BaseModel` (`common.base.BaseModel`):

- UUID primary keys (not integer IDs)
- Automatic timestamps: `created_at`, `updated_at`
- Audit trail: `created_by`, `updated_by`
- Organization isolation: `org = models.ForeignKey(Org)`

### API Endpoint Pattern

```
GET/POST       /api/<module>/              # List/Create
GET/PUT/DELETE /api/<module>/<pk>/         # Detail/Update/Delete
GET/POST       /api/<module>/comment/<pk>/ # Comments
GET/POST       /api/<module>/attachment/<pk>/ # Attachments
```

## Project Structure

```
backend/
├── manage.py
├── pyproject.toml          # Python deps + project metadata (uv-managed)
├── uv.lock                 # Pinned, reproducible dependency tree
├── .python-version         # Python version pin (uv reads this on `uv sync`)
├── crm/                    # Django project settings
│   ├── settings.py
│   ├── urls.py
│   ├── celery.py
│   └── wsgi.py
├── common/                 # Core models and utilities
│   ├── models.py           # User, Org, Profile, etc.
│   ├── base.py             # BaseModel
│   ├── middleware/
│   └── tasks.py            # Celery tasks
│   └── templates/          # Email templates, shipped as package data
├── accounts/
├── leads/
├── contacts/
├── opportunity/
├── cases/
├── tasks/
├── invoices/
├── orders/
├── business_hours/
├── macros/
└── static/
```

> Templates live in their owning app's `templates/` directory rather than a
> project-level one. `TEMPLATES[0]["DIRS"]` is empty on purpose: a `BASE_DIR`
> entry resolves to `site-packages` once the package is installed, where
> nothing is written, and the login emails would not render.

## Development

### Code Quality

```bash
# Lint (E, F and I as backend/ruff.toml selects them)
uv run ruff check .

# Format the tree. `ruff format` is black's output from one tool, and ruff's
# I rules do isort's job, so neither black nor isort is installed here.
uv run ruff format .

# Run tests
uv run pytest
```

CI runs `ruff check .` and `ruff format --check .` as hard steps, so both must pass.

### Managing Dependencies

```bash
# Add a runtime dependency (updates pyproject.toml + uv.lock)
uv add <package>

# Add a dev-only dependency (e.g. test or lint tool)
uv add --group dev <package>

# Remove a dependency
uv remove <package>

# Refresh the lockfile
uv lock --upgrade
```

### Creating a New App

1. Create the app:
   ```bash
   uv run python manage.py startapp myapp
   ```

2. Add to `INSTALLED_APPS` in `crm/settings.py`

3. Create models inheriting from `BaseModel`:
   ```python
   from common.base import BaseModel
   from common.models import Org


   class MyModel(BaseModel):
       org = models.ForeignKey(Org, on_delete=models.CASCADE)
       # ... other fields
   ```

4. Always filter queries by organization:
   ```python
   queryset = MyModel.objects.filter(org=request.profile.org)
   ```

5. Run migrations:
   ```bash
   uv run python manage.py makemigrations
   uv run python manage.py migrate
   ```

## Environment Variables Reference

| Variable | Description |
|----------|-------------|
| `SECRET_KEY` | Django secret key, and the JWT signing key. At least 32 bytes |
| `ENV_TYPE` | Environment type (`dev` or `prod`) |
| `DEBUG` | `True` or `False`. Never `True` in production |
| `ALLOWED_HOSTS` | Comma-separated hostnames. Defaults to `localhost,127.0.0.1` |
| `DBNAME` | PostgreSQL database name |
| `DBUSER` | PostgreSQL username. Must NOT be a superuser, see the RLS section |
| `DBPASSWORD` | PostgreSQL password |
| `DBHOST` | PostgreSQL host |
| `DBPORT` | PostgreSQL port |
| `DB_POOL_ENABLED` | Connection pooling, off by default. See the note below |
| `DB_POOL_MIN_SIZE` | Pool minimum, default `2`. Per process, not per host |
| `DB_POOL_MAX_SIZE` | Pool maximum, default `10`. Per process, not per host |
| `FRONTEND_URL` | The **web app** origin. Every emailed link is built from it |
| `DOMAIN_NAME` | This API's own public origin |
| `GOOGLE_CLIENT_ID` | Google OAuth client id. Required for sign-in |
| `GOOGLE_CLIENT_SECRET` | Google OAuth client secret. Required for sign-in |
| `CORS_ALLOWED_ORIGINS` | Comma-separated browser origins allowed to call the API |
| `CORS_ALLOW_ALL` | Development escape hatch. Leave off in production |
| `CSRF_TRUSTED_ORIGINS` | Comma-separated origins trusted for CSRF |
| `TRUST_PROXY_SSL_HEADER` | Set when running behind a TLS-terminating proxy |
| `DEFAULT_FROM_EMAIL` | Default sender email |
| `ADMIN_EMAIL` | Admin notification email |
| `EMAIL_BACKEND` | Django email backend. Defaults to AWS SES |
| `AWS_SES_REGION_NAME` | AWS SES region |
| `AWS_SES_REGION_ENDPOINT` | AWS SES endpoint |
| `CELERY_BROKER_URL` | Redis URL for Celery broker |
| `CELERY_RESULT_BACKEND` | Redis URL for Celery results |
| `DJANGO_ORG_API_KEY_AUTH` | Enables org API key authentication |

> **`DB_POOL_ENABLED` is not just a performance knob.** RLS context lives in a
> session-scoped variable, so a pooled connection carries the previous tenant's
> org id unless something clears it. The `reset` callback in
> `common/rls/pool.py` is what clears it, and pooling must never be enabled
> without it.

## Troubleshooting

### Database Connection Issues

```bash
# Check PostgreSQL is running
sudo systemctl status postgresql

# Verify database exists
sudo -u postgres psql -l
```

### Migration Issues

```bash
# Show migration status
uv run python manage.py showmigrations

# Reset migrations (development only)
uv run python manage.py migrate --fake <app> zero
```

### Celery Not Processing Tasks

```bash
# Check Redis is running
redis-cli ping

# Check Celery worker logs
uv run celery -A crm worker --loglevel=DEBUG
```

## License

MIT License. See
[LICENSE](https://github.com/django-crm/Django-CRM/blob/master/LICENSE).

The link is absolute on purpose. This file is the package's long description on
PyPI, where a relative `../LICENSE` points outside the distribution and 404s.
