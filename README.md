# Ordo

**Examination operations, ordered from planning to paperwork.**

Ordo is an examination-management platform for educational institutions. It brings academic data preparation, examination timetabling, venue distribution, candidate seating, operational monitoring, and examination documents into one coordinated workflow.

## What Ordo covers

- Academic structures, courses, classes, students, venues, and examination sessions
- Examination timetable generation and review
- Candidate distribution across halls
- Individual seat allocation and adjustment
- Progress monitoring for long-running planning work
- Operational exports, including schedules, seating plans, attendance materials, and related documents
- Role-based workflows for central administrators, department teams, faculty officers, and examination coordinators

## Who it is for

Ordo is designed for institutions that coordinate examinations across multiple departments, courses, venues, and administrative teams. It supports both institution-wide planning and delegated preparation by authorized staff.

## Product status

The documentation reflects the behavior and configuration found in the current repository. Capabilities are identified as verified, partially implemented, planned, inferred, referenced only, or unknown where appropriate. See the product intelligence summary and technical specification before making production, security, or commercial commitments.

## Repository guide

| Path | Purpose |
| --- | --- |
| `frontend/` | Dashboard web application (apex domain). The marketing site lives in a separate repo, `ordo-site`. |
| `ems/` | Examination-management domain and service implementation |
| `core/` | Application configuration and runtime setup |
| `docs/` | Product, marketing, and technical documentation |
| `testing/` | Exploratory testing resources |
| `.env.example` | Local development settings. Copy to `.env`. Production uses `deploy/.env.prod.example`. |

## Local development

Use Python 3.11, the version used by CI and the runtime image. Copy
`.env.example` to `.env` and configure your local PostgreSQL and Redis connections.
After installing dependencies, initialize the platform with:

```sh
python manage.py migrate_platform
python manage.py create_platform_admin --email you@example.com --name "Your Name"
```

`migrate_platform` applies shared and school migrations and registers
`console.localhost` and `api.localhost` against the public schema. Running
`migrate_schemas` alone creates the public tenant but does not register these
domains. If console login returns `School not found.` on an existing database,
run `python manage.py ensure_public_domains`. Run it again after changing
`PLATFORM_BASE_DOMAIN` or `PLATFORM_CONSOLE_SUBDOMAIN`, keeping frontend and
backend host settings consistent.

Start Django, the background worker and scheduler, and the frontend in separate
terminals:

```sh
python manage.py runserver
python manage.py celery all
cd frontend
pnpm install
pnpm dev
```

Open `http://console.localhost:3000` for platform administration. Platform admins
are separate from school user accounts.

The local Celery command uses `solo` on macOS and Windows because Celery 5.3's
default process pool can fail under `spawn` with
`ValueError: not enough values to unpack (expected 3, got 0)` before task code
runs. Solo processes one task at a time, ignores `--concurrency`, and does not
provide prefork task time limits. Linux keeps `prefork`. You can override the
pool with `python manage.py celery all --pool threads -c 4`. When invoking
Celery directly on macOS or Windows, use
`celery -A core worker -l info --pool solo -Q celery,observability` and run beat
separately.

