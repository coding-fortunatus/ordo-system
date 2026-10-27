#!/bin/bash

set -e

echo "Starting EMS-IBR Service..."
echo "SERVICE_TYPE: ${SERVICE_TYPE:-web}"

# Function to wait for PostgreSQL
wait_for_postgres() {
    echo "Waiting for PostgreSQL to be ready..."
    python << END
import sys
import time
import psycopg2
from urllib.parse import urlparse
import os

max_retries = 30
retry_count = 0

database_url = os.environ.get('DATABASE_URL')
if not database_url:
    print("ERROR: DATABASE_URL environment variable is not set")
    sys.exit(1)

# Parse the database URL
result = urlparse(database_url)
username = result.username
password = result.password
database = result.path[1:]
hostname = result.hostname
port = result.port

while retry_count < max_retries:
    try:
        conn = psycopg2.connect(
            dbname=database,
            user=username,
            password=password,
            host=hostname,
            port=port
        )
        conn.close()
        print("PostgreSQL is ready!")
        sys.exit(0)
    except psycopg2.OperationalError as e:
        retry_count += 1
        print(f"PostgreSQL not ready yet (attempt {retry_count}/{max_retries})...")
        time.sleep(2)

print("ERROR: Could not connect to PostgreSQL after maximum retries")
sys.exit(1)
END
}

# Function to wait for Redis
wait_for_redis() {
    echo "Waiting for Redis to be ready..."
    python << END
import sys
import time
import redis
from urllib.parse import urlparse
import os

max_retries = 30
retry_count = 0

redis_url = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')

while retry_count < max_retries:
    try:
        r = redis.from_url(redis_url)
        r.ping()
        print("Redis is ready!")
        sys.exit(0)
    except (redis.ConnectionError, redis.TimeoutError) as e:
        retry_count += 1
        print(f"Redis not ready yet (attempt {retry_count}/{max_retries})...")
        time.sleep(2)

print("ERROR: Could not connect to Redis after maximum retries")
sys.exit(1)
END
}

# An explicit command (e.g. a platform's pre-deploy command, or
# `docker run <image> python manage.py ...`) runs as is, never the web server.
if [ "$#" -gt 0 ]; then
    exec "$@"
fi

# Check SERVICE_TYPE and execute appropriate commands
if [ "${SERVICE_TYPE}" = "migrate" ]; then
    # One shot, run before every release. Web, worker and beat must only
    # start once this exits 0 (Compose service_completed_successfully,
    # Render pre-deploy command). set -e makes any failure exit non zero.
    echo "Running platform migrations..."

    wait_for_postgres

    exec python manage.py migrate_platform

elif [ "${SERVICE_TYPE}" = "backup" ]; then
    # Whole database backup now, then daily at BACKUP_TIME_UTC, with
    # retries (spec 0003, child 0007).
    # Kept out of Celery so it still runs when the workers are broken.
    echo "Starting database backup daemon..."

    wait_for_postgres

    exec python manage.py backup_database --daemon

elif [ "${SERVICE_TYPE}" = "worker" ]; then
    echo "Starting Celery Worker..."
    
    # Wait for dependencies
    wait_for_postgres
    wait_for_redis
    
    # Start Celery worker
    exec celery -A core worker -l info --concurrency=4
    
elif [ "${SERVICE_TYPE}" = "worker-observability" ]; then
    # Telemetry only (spec 0006): the 10 second flush, partitions, and later
    # the host, database and media numbers. One at a time, so flushes never
    # overlap and the shared Postgres never sees two.
    echo "Starting Celery observability worker..."

    wait_for_postgres
    wait_for_redis

    export OBS_PROCESS_ROLE="${OBS_PROCESS_ROLE:-worker-observability}"
    exec celery -A core worker -l info -Q observability --concurrency=1

elif [ "${SERVICE_TYPE}" = "beat" ]; then
    echo "Starting Celery Beat..."
    
    # Wait for dependencies
    wait_for_redis
    
    # Start Celery beat
    exec celery -A core beat -l info
    
else
    # Default: web service. Channels needs ASGI, so we run Daphne instead of
    # Gunicorn — Gunicorn-on-WSGI silently drops WebSocket upgrades.
    echo "Starting Web Service..."

    wait_for_postgres

    # collectstatic materializes admin/DRF static into STATIC_ROOT for
    # WhiteNoise. It already ran at image-build time; re-running is a no-op
    # in the typical case but keeps a volume-mounted copy correct.
    echo "Collecting static files..."
    python manage.py collectstatic --no-input

    # Fails the deploy on errors only (the stock --deploy security checks are
    # warnings). ems.E001: uploaded media must not live on this container's
    # disk, or the logo is lost on redeploy and missing on other instances.
    echo "Running deploy checks..."
    python manage.py check --deploy --fail-level ERROR

    # Migrations are not run here: the one shot migrate step
    # (SERVICE_TYPE=migrate) has already applied them before this starts.

    echo "Starting Daphne (ASGI) on port ${PORT:-8000}..."
    exec daphne \
        -b 0.0.0.0 \
        -p "${PORT:-8000}" \
        --proxy-headers \
        core.asgi:application
fi
