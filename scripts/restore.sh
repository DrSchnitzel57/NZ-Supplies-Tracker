#!/usr/bin/env bash
# Restore a compatible backup + matching image release.
# Usage: bash scripts/restore.sh BACKUP VERSION [--allow-newer-data-loss]
# Never discards newer live work without the explicit flag.
set -euo pipefail

BACKUP="${1:-}"
VERSION="${2:-}"
EXTRA="${3:-}"
if [[ -z "$BACKUP" || -z "$VERSION" ]]; then
  echo "usage: bash scripts/restore.sh BACKUP VERSION [--allow-newer-data-loss]" >&2
  exit 2
fi
if [[ ! -f "$BACKUP" ]]; then
  echo "refusing: backup not found: $BACKUP" >&2
  exit 2
fi
if [[ ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "refusing: VERSION must be pinned X.Y.Z, got '$VERSION'" >&2
  exit 2
fi
if [[ "$EXTRA" != "" && "$EXTRA" != "--allow-newer-data-loss" ]]; then
  echo "refusing: third arg must be --allow-newer-data-loss" >&2
  exit 2
fi
if [[ ! -f .env ]]; then
  echo "refusing: missing .env" >&2
  exit 2
fi

echo "==> verifying backup integrity before touching live data"
docker compose run --rm -v "$(pwd)/$(dirname "$BACKUP"):/restore-src:ro" app \
  python -c "from apps.operations import backup; backup.verify_sqlite_backup('/restore-src/$(basename "$BACKUP")'); print('backup verified')" || {
    echo "restore refused: backup failed checks; live data untouched." >&2
    exit 1
  }

echo "==> pulling matching release image"
docker pull "${IMAGE:-ghcr.io/example/supplies-tracker}:$VERSION"

echo "==> restoring under exclusive maintenance"
docker compose run --rm app python manage.py shell -c "from apps.operations import guards; guards.enter_maintenance(); print('maintenance on')"
trap 'echo "RESTORE FAILED: writes remain closed. Re-run restore or update; check docker compose logs app." >&2' ERR
VERSION="$VERSION" docker compose up -d app
docker compose run --rm app python manage.py migrate --noinput
docker compose run --rm \
  -v "$(pwd)/$(dirname "$BACKUP"):/restore-src:ro" app \
  python manage.py restore_db "/restore-src/$(basename "$BACKUP")" \
    --expected-version "$VERSION" $EXTRA
docker compose up -d app
docker compose exec -T app python manage.py shell -c "from apps.operations import guards; guards.exit_maintenance(); print('maintenance off')"
trap - ERR
echo "restore complete: backup=$(basename "$BACKUP") version=$VERSION"
