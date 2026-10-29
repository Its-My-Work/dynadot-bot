#!/bin/sh
set -e

echo "Running database migrations (Alembic)..."
alembic upgrade head

exec "$@"
