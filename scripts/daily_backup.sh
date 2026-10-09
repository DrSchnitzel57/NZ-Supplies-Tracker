#!/usr/bin/env bash
# Daily host-local backup: verified SQLite copy + 30-day retention.
# Install as a daily cron job on the host (see docs/OPERATIONS.md).
# Keeps the newest verified pre-update/pre-reset recovery backup until
# superseded. Logs to the host log path for operator monitoring.
set -euo pipefail
cd "$(dirname "$0")/.."
STAMP="$(date +%Y%m%d-%H%M%S)"
DEST="/app/data/backups/daily-${STAMP}.sqlite3"
LOG="${DAILY_BACKUP_LOG:-/var/log/supplies-tracker-backup.log}"
{
  echo "[$STAMP] daily backup start"
  docker compose run --rm app python manage.py backup_db --dest "$DEST" --quiesce
  docker compose run --rm app python manage.py prune_backups --days 30
  echo "[$STAMP] daily backup complete: $DEST"
} 2>&1 | tee -a "$LOG"
