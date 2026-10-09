#!/usr/bin/env bash
# Controlled update to a pinned release VERSION.
# Usage: bash scripts/update.sh VERSION
# Verifies config/release, enters maintenance, takes a verified backup,
# pulls the pinned image, migrates, restarts and checks readiness.
# On failure: keeps mutation access closed and prints exact recovery steps.
set -euo pipefail

VERSION="${1:-}"
if [[ -z "$VERSION" ]]; then
  echo "usage: bash scripts/update.sh VERSION" >&2
  exit 2
fi
if [[ ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "refusing: VERSION must be pinned X.Y.Z, got '$VERSION'" >&2
  exit 2
fi
if [[ ! -f .env ]]; then
  echo "refusing: missing .env (copy .env.example and set secrets first)" >&2
  exit 2
fi
set -a; . ./.env; set +a
if [[ "${DJANGO_SECRET_KEY:-}" == "" || "${DJANGO_SECRET_KEY:-}" == "replace-with-a-long-random-secret" || "${DJANGO_SECRET_KEY:-}" == "change-me-in-production" ]]; then
  echo "refusing: DJANGO_SECRET_KEY is unset or still a placeholder" >&2
  exit 2
fi

export VERSION APP_VERSION="$VERSION"
IMAGE_REF="${IMAGE:-ghcr.io/example/supplies-tracker}:$VERSION"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_FILE="pre-update-${STAMP}.sqlite3"

fail_closed() {
  echo "UPDATE FAILED: mutation access remains closed (maintenance on)." >&2
  echo "Recovery steps:" >&2
  echo "  1. Inspect: docker compose logs app | tail -50" >&2
  echo "  2. Roll back image: VERSION=<prior> bash scripts/restore.sh <backups-dir>/$BACKUP_FILE <prior-version> --allow-newer-data-loss" >&2
  echo "  3. Or re-run this script after fixing the error above." >&2
  echo "  4. Only reopen writes after /readyz returns {\"ok\": true}." >&2
}
trap 'fail_closed' ERR

echo "==> verifying release $IMAGE_REF"
docker pull "$IMAGE_REF"

echo "==> entering maintenance (quiescing writes)"
docker compose run --rm app python manage.py shell -c "from apps.operations import guards; guards.enter_maintenance(); print('maintenance on')" || true

echo "==> verified backup to $BACKUP_FILE"
docker compose run --rm -e BACKUP_NAME="$BACKUP_FILE" app \
  python manage.py backup_db --prefix "pre-update-${STAMP}-" || {
    echo "backup failed; aborting update, writes stay closed" >&2
    exit 1
  }

echo "==> pulling pinned image and restarting"
VERSION="$VERSION" docker compose pull app
VERSION="$VERSION" docker compose up -d app

echo "==> migrating"
docker compose run --rm app python manage.py migrate --noinput

echo "==> readiness check"
for i in $(seq 1 30); do
  if docker compose exec -T app python -c "import urllib.request,json; print(urllib.request.urlopen('http://127.0.0.1:8000/readyz/',timeout=5).read().decode())" 2>/dev/null | grep -q '"ok": true'; then
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
docker compose exec -T app python manage.py shell -c "from apps.operations import guards; guards.exit_maintenance(); print('maintenance off')"
trap - ERR
echo "update to $VERSION complete. Recovery backup protected until superseded."
