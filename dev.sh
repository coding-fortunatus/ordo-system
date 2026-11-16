#!/usr/bin/env bash
# Start Django, Celery (worker + beat) and the Vite frontend together.
# Ctrl+C stops everything.
set -e
cd "$(dirname "$0")"
source .venv/bin/activate

trap 'kill 0' EXIT

python manage.py runserver 2>&1 | sed 's/^/[django] /' &
python manage.py celery all 2>&1 | sed 's/^/[celery] /' &
(cd frontend && pnpm dev) 2>&1 | sed 's/^/[web]    /' &

wait
