#!/usr/bin/env bash
# Daily host-local backup: verified SQLite copy + 30-day retention.
# Install as a daily cron job on the host (see docs/OPERATIONS.md).
# Keeps the newest pre-update recovery backup until superseded.
set -euo pipefail
cd "$(dirname "$0")/.."
STAMP="$(date +%Y%m%d-%H%M%S)"
docker compose run --rm app python manage.py backup_db --prefix "daily-${STAMP}-"
docker compose run --rm app python manage.py prune_backups --days 30
echo "daily backup complete: $STAMP"
