#!/usr/bin/env bash
# Restore a compatible backup + matching image release.
# Usage: bash scripts/restore.sh BACKUP VERSION [--allow-newer-data-loss]
# Never discards newer live work without the explicit flag.
# Order is always: verify, maintenance, restore, migrate, readiness, reopen.
set -euo pipefail

BACKUP="${1:-}"
VERSION="${2:-}"
EXTRA="${3:-}"
if [[ -z "$BACKUP" || -z "$VERSION" ]]; then
  echo "usage: bash scripts/restore.sh BACKUP VERSION [--allow-newer-data-loss]" >&2
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

# Read only the keys this script needs; never execute .env as shell.
env_val() {
  grep -E "^[[:space:]]*$1[[:space:]]*=" .env 2>/dev/null | tail -n 1 \
    | sed -E 's/^[^=]*=[[:space:]]*//; s/[[:space:]]*$//; s/^["'"'"']//; s/["'"'"']$//' || true
}
SECRET="$(env_val DJANGO_SECRET_KEY)"
if [[ -z "$SECRET" || "$SECRET" == "replace-with-a-long-random-secret" || "$SECRET" == "change-me-in-production" || "$SECRET" == "dev-insecure-secret-key-change-me" ]]; then
  echo "refusing: DJANGO_SECRET_KEY is unset or still a placeholder" >&2
  exit 2
fi
IMAGE_BASE="$(env_val IMAGE)"
IMAGE_BASE="${IMAGE_BASE:-ghcr.io/example/supplies-tracker}"

# Canonicalize the backup path so the container mount is exact (works with
# absolute paths and directories containing spaces).
BACKUP_ABS="$(realpath "$BACKUP" 2>/dev/null || echo "")"
if [[ -z "$BACKUP_ABS" || ! -f "$BACKUP_ABS" ]]; then
  echo "refusing: backup not found: $BACKUP" >&2
  exit 2
fi
BACKUP_DIR="$(dirname "$BACKUP_ABS")"
BACKUP_NAME="$(basename "$BACKUP_ABS")"
MAINT_TOKEN=""

trap 'echo "RESTORE FAILED: writes remain closed. Re-run restore or update; check docker compose logs app." >&2' ERR

echo "==> verifying backup integrity before touching live data"
docker compose run --rm -v "$BACKUP_DIR:/restore-src:ro" app \
  python -c "from apps.operations import backup; backup.verify_sqlite_backup('/restore-src/$BACKUP_NAME'); print('backup verified')"

echo "==> pulling matching release image"
docker pull "${IMAGE_BASE}:$VERSION"

echo "==> restoring under exclusive maintenance"
ENTER_OUT="$(VERSION="$VERSION" docker compose run --rm app python manage.py maintenance --enter --owner "restore-$VERSION" --reason "restore $BACKUP_NAME")"
echo "$ENTER_OUT"
MAINT_TOKEN="$(echo "$ENTER_OUT" | sed -n 's/.*token=//p' | tr -d '[:space:]')"
if [[ -z "$MAINT_TOKEN" ]]; then
  echo "refusing: could not acquire maintenance window; live data untouched" >&2
  exit 1
fi
VERSION="$VERSION" docker compose up -d app
# restore_db performs: verify, preserve, restore, migrate, readiness — and
# only then (via this script) do we reopen writes below.
VERSION="$VERSION" docker compose run --rm \
  -v "$BACKUP_DIR:/restore-src:ro" \
  -e APP_VERSION="$VERSION" app \
  python manage.py restore_db "/restore-src/$BACKUP_NAME" \
    --expected-version "$VERSION" $EXTRA
VERSION="$VERSION" docker compose up -d app
echo "==> readiness gate before reopening"
for i in $(seq 1 30); do
  if docker compose exec -T app python -c "import urllib.request; req = urllib.request.Request('http://127.0.0.1:8000/readyz/', headers={'X-Forwarded-Proto': 'https'}); print(urllib.request.urlopen(req, timeout=5).read().decode())" 2>/dev/null | grep -q '"ok": true'; then
    echo "restored database is serving"
    break
  fi
  if [[ "$i" == "30" ]]; then
    echo "readiness failed after restore; writes stay closed" >&2
    exit 1
  fi
  sleep 2
done
docker compose exec -T app python manage.py maintenance --exit --token "$MAINT_TOKEN"
MAINT_TOKEN=""
trap - ERR
echo "restore complete: backup=$BACKUP_NAME version=$VERSION"
