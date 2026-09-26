# BottleCRM: Open Source Django CRM for Startups & Enterprises

A free, self-hosted, multi-tenant CRM built with Django REST Framework, SvelteKit and Flutter.

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.12+-green.svg)
![Django](https://img.shields.io/badge/django-6.x-green.svg)
![SvelteKit](https://img.shields.io/badge/sveltekit-2.x-orange.svg)
![Svelte](https://img.shields.io/badge/svelte-5-orange.svg)
![Flutter](https://img.shields.io/badge/flutter-3.44+-blue.svg)
![Coverage](./coverage-badge.svg)

**BottleCRM is an open source CRM you run on your own infrastructure.** It covers the full
customer lifecycle: leads, accounts, contacts, opportunities, support tickets, tasks and
invoices: through a SvelteKit web app, a native Flutter mobile app, and a documented REST API
sharing one Django backend. PostgreSQL Row-Level Security isolates each organization's data at
the database layer, so a single deployment serves one startup or hundreds of tenants.

No per-seat pricing, no user caps, no feature paywall. MIT licensed: fork it, self-host it, own
your data.

**[Try it free →](https://bottlecrm.io/)** · [Features](https://bottlecrm.io/features) · [Product docs](https://bottlecrm.io/docs) · [Pricing](https://bottlecrm.io/pricing) · [Migrate from Salesforce or HubSpot](https://bottlecrm.io/migration)

(That's the marketing site's product documentation. For the technical documentation (self-hosting, architecture, the API, contributing) see [Documentation](#documentation) below.)

## Why BottleCRM

- **Free forever, MIT licensed**: unlimited users and records, no subscription. A genuinely
  self-hosted CRM alternative to Salesforce, HubSpot and Pipedrive.
- **Multi-tenant by design**: PostgreSQL Row-Level Security enforces tenant isolation in the
  database, not just in application code. Run it for a single company or as a SaaS for many.
- **Agent-ready REST API**: point Claude, Cursor, Codex, Gemini or any agent at the API with a
  personal access token and let it search, create and update records *as you*, inheriting your
  role, org and permissions. An OpenAPI schema ships with it, so the agent can discover the
  endpoints itself.
- **Web, native mobile and API**: one Django REST backend behind a Svelte 5 web app and a
  Flutter app for iOS and Android.
- **A stack you can actually hack on**: Django 6 / DRF and Svelte 5, not a bespoke in-house
  framework. If your team writes Python, it can extend this on day one.
- **Real support tooling**: a full helpdesk with SLA timers, approvals, escalations, macros and
  a knowledge base, not a bolted-on ticket list.

## How it compares

| | BottleCRM | SaaS CRM (Salesforce, HubSpot) | Typical open source CRM |
|---|---|---|---|
| **Cost** | Free, unlimited users | Per seat, per month | Free core, often paid tiers |
| **Hosting** | Self-hosted (managed hosting available) | Vendor cloud only | Self-hosted |
| **Data ownership** | Total. It is your database | Vendor-controlled | Total |
| **Multi-tenancy** | Database-level RLS | Not applicable | Uncommon |
| **Native mobile app** | Yes, Flutter, iOS + Android | Yes | Uncommon |
| **AI agent access** | Full REST API, per-user tokens | Proprietary add-ons | Uncommon |
| **Stack** | Django REST + SvelteKit | Closed source | Varies |
| **License** | MIT | Proprietary | Varies, often GPL/AGPL |

## Quick start

```bash
git clone https://github.com/django-crm/Django-CRM.git
cd Django-CRM
docker compose up --build
```

Frontend at http://localhost:5181, API and Swagger UI at http://localhost:8000/swagger-ui/. See
the [Docker quick start guide](docs/getting-started/docker-quick-start.md) for the default admin
login, loading demo data, and what each service does.

## Documentation

The full documentation (setup, self-hosting, architecture, the REST API, and contributing) is
built from this repository's [`docs/`](docs/) with MkDocs Material and publishes to Read the Docs
at <https://django-crm.readthedocs.io/en/latest/>. Four starting points:

- **[Getting started](docs/getting-started/docker-quick-start.md)**: Docker and manual setup, first sign-in, demo data.
- **[Self-hosting](docs/self-hosting/requirements.md)**: requirements, production deployment, PostgreSQL and Row-Level Security, backups, security hardening.
- **[API reference](docs/api/conventions.md)**: authentication, conventions, and every documented endpoint.
- **[Contributing](docs/contributing/development-setup.md)**: local development setup, testing, code style, and how to open a pull request.

## Contributing

We welcome contributions! See [CONTRIBUTING.md](CONTRIBUTING.md) for setup instructions, development checks, and pull request guidance.

## Community

- **Issues**: [GitHub Issues](https://github.com/django-crm/Django-CRM/issues)
- **Twitter**: [@micropyramid](https://twitter.com/micropyramid)
- **Commercial Support**: [Contact us](https://micropyramid.com/contact/)

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Sponsors

We are grateful to the sponsors who support BottleCRM's continued development and maintenance.

- [MicroPyramid](https://micropyramid.com/)

We warmly welcome new sponsors. If you would like to support BottleCRM and help the project grow, please [get in touch](https://micropyramid.com/contact/).

## Contributors

This project exists thanks to all the people who contributed.

[View all BottleCRM contributors](https://github.com/django-crm/Django-CRM/graphs/contributors).
