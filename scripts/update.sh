#!/usr/bin/env bash
# Controlled update to a pinned release VERSION.
# Usage: bash scripts/update.sh VERSION
# Verifies config/release, enters maintenance, takes a verified backup,
# pulls the pinned image, migrates, restarts and checks readiness.
# On failure: keeps mutation access closed and prints exact recovery steps.
set -euo pipefail

REQUESTED_VERSION="${1:-}"
if [[ -z "$REQUESTED_VERSION" ]]; then
  echo "usage: bash scripts/update.sh VERSION" >&2
  exit 2
fi
if [[ ! "$REQUESTED_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "refusing: VERSION must be pinned X.Y.Z, got '$REQUESTED_VERSION'" >&2
  exit 2
fi
if [[ ! -f .env ]]; then
  echo "refusing: missing .env (copy .env.example and set secrets first)" >&2
  exit 2
fi

# Read only the keys this script needs; never execute .env as shell, and the
# requested pin always wins over anything in the file.
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
VERSION="$REQUESTED_VERSION"
export VERSION
export APP_VERSION="$VERSION"
IMAGE_REF="${IMAGE_BASE}:${VERSION}"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_FILE="pre-update-${STAMP}.sqlite3"
MAINT_TOKEN=""

fail_closed() {
  echo "UPDATE FAILED: mutation access remains closed (maintenance on)." >&2
  echo "Recovery steps:" >&2
  echo "  1. Inspect: docker compose logs app | tail -50" >&2
  echo "  2. Roll back image: bash scripts/restore.sh <backups-dir>/$BACKUP_FILE <prior-version> --allow-newer-data-loss" >&2
  echo "  3. Or re-run this script after fixing the error above." >&2
  echo "  4. Only reopen writes after /readyz returns {\"ok\": true}." >&2
  if [[ -n "$MAINT_TOKEN" ]]; then
    echo "  5. The maintenance token for this run was: $MAINT_TOKEN" >&2
    echo "     Release it only after recovery: docker compose run --rm app python manage.py maintenance --exit --token $MAINT_TOKEN" >&2
  fi
}
trap 'fail_closed' ERR

echo "==> verifying release $IMAGE_REF"
docker pull "$IMAGE_REF"

echo "==> entering maintenance (quiescing writes)"
ENTER_OUT="$(docker compose run --rm app python manage.py maintenance --enter --owner "update-$VERSION" --reason "update to $VERSION")"
echo "$ENTER_OUT"
MAINT_TOKEN="$(echo "$ENTER_OUT" | sed -n 's/.*token=//p' | tr -d '[:space:]')"
if [[ -z "$MAINT_TOKEN" ]]; then
  echo "refusing: could not acquire maintenance window; aborting update" >&2
  exit 1
fi

echo "==> verified backup to $BACKUP_FILE"
docker compose run --rm app \
  python manage.py backup_db --dest "/app/data/backups/$BACKUP_FILE" --quiesce \
    --owner "update-$VERSION" --token "$MAINT_TOKEN"

echo "==> pulling pinned image and restarting"
VERSION="$VERSION" docker compose pull app
VERSION="$VERSION" docker compose up -d app

echo "==> migrating"
docker compose run --rm app python manage.py migrate --noinput

echo "==> readiness check"
for i in $(seq 1 30); do
  if docker compose exec -T app python -c "import urllib.request; req = urllib.request.Request('http://127.0.0.1:8000/readyz/', headers={'X-Forwarded-Proto': 'https'}); print(urllib.request.urlopen(req, timeout=5).read().decode())" 2>/dev/null | grep -q '"ok": true'; then
    echo "ready: version $VERSION serving"
    break
  fi
  if [[ "$i" == "30" ]]; then
    echo "readiness failed after 30 tries" >&2
    exit 1
  fi
  sleep 2
done

echo "==> exiting maintenance"
docker compose exec -T app python manage.py maintenance --exit --token "$MAINT_TOKEN"
MAINT_TOKEN=""
trap - ERR
echo "update to $VERSION complete. Recovery backup protected until superseded."
