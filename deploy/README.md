# Ordo production on the VPS

This runbook covers bringing up `deploy/compose.prod.yml` (spec 0003 slice 12), deploying, and the backup and restore drill (spec 0003, child 0007). Each step is a recommendation. Adjust to your setup.

## What runs

| Service | What it does |
|---|---|
| `traefik` | The only thing with open ports (80, 443). Port 80 redirects to 443. One wildcard certificate for `ordo.app` and `*.ordo.app` through Cloudflare DNS. Routes `/api`, `/ws`, `/media` to `api`, everything else to `dashboard` (`traefik/dynamic.yml`). |
| `postgres` | Postgres 16 with `max_locks_per_transaction=256`, data on the `pgdata` volume. |
| `redis` | Celery broker and Channels layer. |
| `migrate` | One shot. Shared migrations, then every live school, under a lock. Everything below waits for it to exit 0. |
| `api` | Django on Daphne (REST and WebSockets). |
| `worker`, `beat` | Celery. |
| `backup` | Whole database backup to R2 when it starts, then daily at `BACKUP_TIME_UTC`, with retries. Never deletes a backup. |
| `dashboard` | The school dashboard and platform console. |

## First boot (fresh database, no schools)

1. **Cloudflare DNS** for `ordo.app`, both **DNS only** (grey cloud): `A @` to the VPS IP, and `A *` to the VPS IP. Create an API token scoped to Zone, DNS, Edit on `ordo.app` only.
2. **R2**: create two private buckets, `ordo-exports` and `ordo-backups`. Then set up `ordo-backups` once in the Cloudflare dashboard (R2, the bucket, Settings):
   - **Bucket lock rule** on prefix `db/`: retain for **14 days**. Nothing under `db/` can be deleted or overwritten in that time, not even by a VPS someone has taken over.
   - **Lifecycle rules**: delete objects with prefix `db/daily/` **15 days** after upload, and `db/weekly/` **57 days** after upload. R2 owns retention. The app never deletes a backup.
   - **A backup token** with *Object Read & Write* on `ordo-backups` only. Put it in `BACKUP_S3_ACCESS_KEY_ID` and `BACKUP_S3_SECRET_ACCESS_KEY`. The export token goes in `EXPORT_S3_*` and never needs this bucket.
   - **2FA on the Cloudflare account.** A Cloudflare admin can remove the bucket lock, so the account is what guards it. Never put an R2 admin credential on the VPS.
3. **Outside monitor**: create a check at a dead man's switch service such as Healthchecks.io, with a **24 hour period** and a **2 hour grace**, emailing you. Put its ping URL in `BACKUP_HEARTBEAT_URL`. You get an email at once when a backup attempt fails (`/fail`), and when no ping arrives for 26 hours, which also catches the whole VPS being down.
4. **Email (Brevo)**. Invites and platform alerts go out over Brevo's SMTP relay.
   - In Brevo, under **Senders, Domains & Dedicated IPs**, **Domains**, add `ordo.app`. Add the records it lists (the `brevo-code` TXT, the DKIM records, and a DMARC TXT if you have none) in Cloudflare DNS, as **DNS only**. Then click **Authenticate**. Mail from an unauthenticated domain gets rejected or lands in spam.
   - Under **Senders**, add `no-reply@ordo.app`, or whatever `DEFAULT_FROM_EMAIL` uses.
   - Under **SMTP & API**, **SMTP**, generate an SMTP key. Put the **Login** shown there in `EMAIL_HOST_USER` and the key in `EMAIL_HOST_PASSWORD`. A new Brevo account may have to ask support to turn on transactional sending before the relay accepts mail.
   - Once the stack is up (step 6), send a test:
     ```sh
     docker compose -f deploy/compose.prod.yml run --rm api python manage.py sendtestemail you@example.com
     ```
     Then check **Transactional**, **Logs** in Brevo if it doesn't arrive. A send that fails in the app is retried 3 times by the worker, then logged as `Giving up emailing ...` (`docker compose -f deploy/compose.prod.yml logs worker`).
5. **Secrets** on the VPS, from the repo root:
   ```sh
   cp deploy/.env.example deploy/.env            # Traefik and Postgres
   cp deploy/.env.prod.example deploy/.env.prod  # Django services
   chmod 600 deploy/.env deploy/.env.prod
   ```
   Fill both. The password in `DATABASE_URL` must match `POSTGRES_PASSWORD`. Then save a copy of both files in your password manager, off the VPS (see *What recovery needs* below).
6. **Start it**:
   ```sh
   docker compose -f deploy/compose.prod.yml up -d --build
   docker compose -f deploy/compose.prod.yml ps        # all healthy, migrate exited 0
   ```
7. **Check the certificate** (AC-10). It should list `*.ordo.app`:
   ```sh
   openssl s_client -connect x.ordo.app:443 -servername x.ordo.app </dev/null 2>/dev/null \
     | openssl x509 -noout -subject -ext subjectAltName
   ```
8. **First Owner**:
   ```sh
   docker compose -f deploy/compose.prod.yml run --rm api \
     python manage.py create_platform_admin --email you@ordo.app --name "Your Name"
   ```
   Then sign in at `https://console.ordo.app` (or whatever `PLATFORM_CONSOLE_SUBDOMAIN` is set to) and enrol your authenticator.
9. **Run the restore drill below before you onboard the first school.** It's the launch blocker.

## Deploying a new release

```sh
C="docker compose -f deploy/compose.prod.yml"
git pull
# Stop the sidecar, so no dump is still running when the new migrate starts
# (a running dump blocks schema changes).
$C stop backup
# A backup from before the migrations, in case a release goes wrong.
$C run --rm backup python manage.py backup_database
# Stamp the release on captured errors (spec 0004), then rebuild.
export ORDO_RELEASE=$(git rev-parse --short HEAD)
$C up -d --build
```

If `migrate` fails, `api`, `worker`, `beat` and `backup` stay on the previous release. Read `$C logs migrate`, fix forward, and deploy again.

## Error observability

Ordo records server errors, failed jobs and tasks, logged errors, browser errors and WebSocket problems per school (spec 0004). The console's **Health** page shows each school as healthy, degraded or failing, and each school's page has an Errors section. Owners get an email when a new error appears or a school starts failing.

- **Release id.** `ORDO_RELEASE` is a build argument for both images (the dashboard gets it as `VITE_RELEASE`). Without it, errors are stamped `dev`.
- **Health needs beat and Redis.** `observe_health` runs every 5 minutes on the worker, from `beat`, and reads counters from Redis. If the Health page says the status is out of date, check `$C ps worker beat redis`.
- **Off switch.** `OBSERVABILITY_ENABLED=False` in `.env.prod` turns off all capture, counting and alert emails.
- **Retention.** Stored occurrences are kept 30 days, hourly counts 90 days, and resolved or ignored groups 90 days after they were last seen. Purging a school removes all of its error data.
- The other settings, all optional, are listed at the end of `.env.prod.example`.

## Backups

- Every day at `BACKUP_TIME_UTC` (default 01:00 UTC, 02:00 in Lagos), and each time the `backup` container starts, one `pg_dump` of the whole database (public schema plus every school) goes to `BACKUP_BUCKET` at `db/daily/ordo-<UTC time>.dump`, with a manifest beside it (`.json`: the schema list and school count, read in the same snapshot as the dump). The first good backup of each UTC Sunday is also copied to `db/weekly/`.
- A backup counts only when `pg_restore --list` reads the archive and the bucket shows the object at its full size. Only then does it write the heartbeat file and ping the monitor.
- A failed attempt is logged and posts its error to the monitor (`/fail`), so you get an email. The sidecar retries up to 3 times, an hour apart. The next 01:00 run always starts on time.
- Retention is R2's job (lifecycle rules above): at least 14 days of dailies and about 8 weeklies. The bucket lock stops anyone deleting a backup for its first 14 days.
- `$C ps` shows `backup` as **unhealthy** when the last good backup is more than 26 hours old. That is informational only; the monitor is what alerts you. See why with `$C logs backup`.
- To back up right now: `$C exec backup python manage.py backup_database`. A one off run makes one attempt and exits non zero if it fails.

## Restore drill

You run this once before the first school is onboarded (the launch blocker), then every quarter. It restores the newest backup into a scratch database next to the live one and compares it with that backup's manifest. It never touches the live database: the command refuses the live server's database without `--into-live`, and refuses any target that isn't empty.

```sh
C="docker compose -f deploy/compose.prod.yml"

# 1. An empty scratch database on the same server.
$C exec postgres createdb -U ordo ordo_restore_drill

# 2. Restore the newest daily into it and compare with its manifest.
#    Use your real password in the URL. It prints both sides and exits
#    non zero if they differ.
$C exec backup python manage.py restore_database --latest --verify \
  --target-url postgres://ordo:<password>@postgres:5432/ordo_restore_drill

# 3. Clean up.
$C exec postgres dropdb -U ordo ordo_restore_drill
```

Then check by hand:

4. In the Cloudflare dashboard, the `db/` bucket lock rule and both lifecycle rules still exist on `ordo-backups`.
5. Deleting a fresh backup with the backup token fails (the lock works):
   ```sh
   $C exec backup python manage.py shell -c "from tenancy.backups import _bucket, latest_key; from django.conf import settings; c = _bucket(); c.delete_object(Bucket=settings.BACKUP_BUCKET, Key=latest_key(c))"
   ```
   This should fail with an access or lock error. If it succeeds, the lock is missing: fix it before going on.
6. The copy of `deploy/.env` and `deploy/.env.prod` in your password manager matches the files on the VPS.

Record each drill here:

| Date | Backup key | Result (verify, bucket rules, delete refused, secrets copy) | Who |
|---|---|---|---|
| | | | |

## Disaster recovery (new server)

Restore your `deploy/.env` and `deploy/.env.prod` from the password manager, bring up only Postgres, restore the newest backup into the empty `ordo` database, then start the rest:

```sh
C="docker compose -f deploy/compose.prod.yml"
$C up -d postgres
$C run --rm --no-deps backup sh -c \
  'python manage.py restore_database --latest --verify --target-url "$DATABASE_URL" --into-live'
$C up -d --build
```

## What recovery needs besides the dump

- The secrets in `deploy/.env` and `deploy/.env.prod`. The essential ones are `SECRET_KEY`, `PLATFORM_TOTP_KEY` (losing it locks every admin out of 2FA) and the R2 and Cloudflare tokens. Keep a current copy of both files in a password manager, off the VPS.
- School uploads live in Cloudinary and are not in the dump. Redis holds only queues and is not backed up.
- `pg_dump` in the API image must match the Postgres major version: change `PG_MAJOR` in the `Dockerfile` and the `postgres` image tag together.
