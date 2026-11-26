#!/bin/sh
# Container start: migrate, seed (idempotent: inserts only missing seed rows), then serve.
set -e
alembic upgrade head
python -m app.seed
# --no-access-log: the app logs one JSON access line per request itself (app/observability.py).
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips="*" --no-access-log
