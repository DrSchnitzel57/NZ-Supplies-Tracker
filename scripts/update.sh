#!/usr/bin/env bash
# Controlled update to a pinned release VERSION.
# Usage: bash scripts/update.sh VERSION [--takeover]
# Verifies config/release, enters maintenance, takes a verified backup,
# pulls the pinned image, migrates, restarts and checks readiness.
# On failure: keeps mutation access closed and prints exact recovery steps.
# --takeover explicitly takes over a window held by a crashed prior run
# (audited); without it a held window aborts instead of being shared.
set -euo pipefail

REQUESTED_VERSION="${1:-}"
TAKEOVER="${2:-}"
if [[ -z "$REQUESTED_VERSION" ]]; then
  echo "usage: bash scripts/update.sh VERSION [--takeover]" >&2
  exit 2
fi
if [[ ! "$REQUESTED_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "refusing: VERSION must be pinned X.Y.Z, got '$REQUESTED_VERSION'" >&2
  exit 2
fi
if [[ -n "$TAKEOVER" && "$TAKEOVER" != "--takeover" ]]; then
  echo "refusing: second arg must be --takeover" >&2
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
# Canonical image contract: IMAGE_BASE (or legacy IMAGE) is a repository
# WITHOUT a tag; VERSION supplies the tag. A tag in the base is refused so
# Compose can never run an image the operator did not pin.
IMAGE_BASE="$(env_val IMAGE_BASE)"
if [[ -z "$IMAGE_BASE" ]]; then
  IMAGE_BASE="$(env_val IMAGE)"
fi
IMAGE_BASE="${IMAGE_BASE:-ghcr.io/example/supplies-tracker}"
if [[ "$IMAGE_BASE" == *:* ]]; then
  echo "refusing: IMAGE_BASE/IMAGE must be a repository without a tag, got '$IMAGE_BASE'" >&2
  exit 2
fi
VERSION="$REQUESTED_VERSION"
export VERSION
export APP_VERSION="$VERSION"
export IMAGE_BASE
IMAGE_REF="${IMAGE_BASE}:${VERSION}"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_FILE="pre-update-${STAMP}.sqlite3"
MAINT_OWNER="update-$VERSION"
MAINT_TOKEN=""

persist_version() {
  # Durably record the successful release pin so a later plain
  # `docker compose up` runs the same release instead of reverting to the
  # old .env VERSION. Atomic (tmp + rename) and only called on success.
  local tmp
  tmp="$(mktemp .env.tmp.XXXXXX)"
  if grep -qE '^[[:space:]]*VERSION[[:space:]]*=' .env; then
    sed -E "s/^[[:space:]]*VERSION[[:space:]]*=.*/VERSION=$VERSION/" .env > "$tmp"
  else
    cat .env > "$tmp"
    echo "VERSION=$VERSION" >> "$tmp"
  fi
  mv "$tmp" .env
}

fail_closed() {
  echo "UPDATE FAILED: mutation access remains closed (maintenance on, owner=$MAINT_OWNER)." >&2
  echo "Recovery steps (writes stay closed until readiness passes):" >&2
  echo "  1. Inspect: docker compose logs app | tail -50" >&2
  echo "  2. Roll back database+image with explicit audited takeover of this window:" >&2
  echo "     bash scripts/restore.sh <backups-dir>/$BACKUP_FILE <prior-version> --allow-newer-data-loss --takeover" >&2
  echo "  3. Or retry this update after fixing the error, taking over the held window:" >&2
  echo "     bash scripts/update.sh $VERSION --takeover" >&2
  echo "  4. Only reopen writes after /readyz returns {\"ok\": true}." >&2
  if [[ -n "$MAINT_TOKEN" ]]; then
    echo "  5. This run holds owner=$MAINT_OWNER token=$MAINT_TOKEN" >&2
    echo "     Release it only after recovery: docker compose run --rm app python manage.py maintenance --exit --token $MAINT_TOKEN" >&2
    echo "     If the token was lost, take over explicitly (audited, keeps writes closed):" >&2
    echo "     docker compose run --rm app python manage.py maintenance --enter --owner operator --reason 'crash recovery' --force" >&2
  else
    echo "  5. No window was acquired by this run; check who holds it:" >&2
    echo "     docker compose run --rm app python manage.py maintenance --check" >&2
  fi
}
trap 'fail_closed' ERR

echo "==> verifying release $IMAGE_REF"
docker pull "$IMAGE_REF"

echo "==> entering maintenance (quiescing writes)"
if [[ "$TAKEOVER" == "--takeover" ]]; then
  ENTER_OUT="$(docker compose run --rm app python manage.py maintenance --enter --owner "$MAINT_OWNER" --reason "update to $VERSION" --force)"
else
  ENTER_OUT="$(docker compose run --rm app python manage.py maintenance --enter --owner "$MAINT_OWNER" --reason "update to $VERSION")"
fi
echo "$ENTER_OUT"
MAINT_TOKEN="$(echo "$ENTER_OUT" | sed -n 's/.*token=//p' | tr -d '[:space:]')"
if [[ -z "$MAINT_TOKEN" ]]; then
  echo "refusing: could not acquire maintenance window; aborting update" >&2
  echo "If a crashed prior run holds the window, inspect it and retry with --takeover:" >&2
  echo "  docker compose run --rm app python manage.py maintenance --check" >&2
  exit 1
fi

echo "==> verified backup to $BACKUP_FILE"
docker compose run --rm app \
  python manage.py backup_db --dest "/app/data/backups/$BACKUP_FILE" --quiesce \
    --owner "$MAINT_OWNER" --token "$MAINT_TOKEN"

echo "==> pulling pinned image and restarting"
VERSION="$VERSION" IMAGE_BASE="$IMAGE_BASE" docker compose pull app
VERSION="$VERSION" IMAGE_BASE="$IMAGE_BASE" docker compose up -d app

echo "==> migrating"
docker compose run --rm app python manage.py migrate --noinput

echo "==> readiness check"
for i in $(seq 1 30); do
  if docker compose exec -T app python -c "import urllib.request; req = urllib.request.Request('http://127.0.0.1:8000/readyz/', headers={'X-Forwarded-Proto': 'https'}); print(urllib.request.urlopen(req, timeout=5).read().decode())" 2>/dev/null | grep -q '"ok": true'; then
    # Confirm the serving release is the requested one, not a stale image.
    if docker compose exec -T app python -c "import urllib.request, json; req = urllib.request.Request('http://127.0.0.1:8000/readyz/', headers={'X-Forwarded-Proto': 'https'}); print(urllib.request.urlopen(req, timeout=5).read().decode())" 2>/dev/null | grep -q "\"version\": *\"$VERSION\""; then
      echo "ready: version $VERSION serving"
      break
    fi
    echo "readiness reports wrong version; waiting for $VERSION" >&2
  fi
  if [[ "$i" == "30" ]]; then
    echo "readiness failed after 30 tries" >&2
    exit 1
  fi
  sleep 2
done

echo "==> persisting release pin and exiting maintenance"
persist_version
docker compose exec -T app python manage.py maintenance --exit --token "$MAINT_TOKEN"
MAINT_TOKEN=""
trap - ERR
echo "update to $VERSION complete. Recovery backup protected until superseded."
