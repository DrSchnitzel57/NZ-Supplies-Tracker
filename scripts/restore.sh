#!/usr/bin/env bash
# Restore a compatible backup + matching image release.
# Usage: bash scripts/restore.sh BACKUP VERSION [--allow-newer-data-loss] [--takeover]
# Never discards newer live work without the explicit flag.
# Order is always: verify, stop writers, restore, migrate, readiness, reopen.
# restore_db owns the maintenance window across restore+migrate and prints
# its token; this script releases it only after its own readiness gate.
# --takeover explicitly takes over a window held by a failed update (audited,
# writes stay closed); without it a held window aborts instead of sharing.
set -euo pipefail

BACKUP="${1:-}"
VERSION="${2:-}"
EXTRA="${3:-}"
TAKEOVER="${4:-}"
if [[ -z "$BACKUP" || -z "$VERSION" ]]; then
  echo "usage: bash scripts/restore.sh BACKUP VERSION [--allow-newer-data-loss] [--takeover]" >&2
  exit 2
fi
if [[ ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "refusing: VERSION must be pinned X.Y.Z, got '$VERSION'" >&2
  exit 2
fi
if [[ "$EXTRA" != "" && "$EXTRA" != "--allow-newer-data-loss" && "$EXTRA" != "--takeover" ]]; then
  echo "refusing: third arg must be --allow-newer-data-loss or --takeover" >&2
  exit 2
fi
if [[ "$TAKEOVER" != "" && "$TAKEOVER" != "--takeover" && "$TAKEOVER" != "--allow-newer-data-loss" ]]; then
  echo "refusing: fourth arg must be --takeover or --allow-newer-data-loss" >&2
  exit 2
fi
# Normalize: allow either order for the two optional flags.
ALLOW_LOSS=""
FORCE=""
for arg in "$EXTRA" "$TAKEOVER"; do
  if [[ "$arg" == "--allow-newer-data-loss" ]]; then ALLOW_LOSS="--allow-newer-data-loss"; fi
  if [[ "$arg" == "--takeover" ]]; then FORCE="--force-takeover"; fi
done
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
IMAGE_BASE="$(env_val IMAGE_BASE)"
if [[ -z "$IMAGE_BASE" ]]; then
  IMAGE_BASE="$(env_val IMAGE)"
fi
IMAGE_BASE="${IMAGE_BASE:-ghcr.io/example/supplies-tracker}"
if [[ "$IMAGE_BASE" == *:* ]]; then
  echo "refusing: IMAGE_BASE/IMAGE must be a repository without a tag, got '$IMAGE_BASE'" >&2
  exit 2
fi

# Canonicalize the backup path so the container mount is exact (works with
# absolute paths and directories containing spaces).
BACKUP_ABS="$(realpath "$BACKUP" 2>/dev/null || echo "")"
if [[ -z "$BACKUP_ABS" || ! -f "$BACKUP_ABS" ]]; then
  echo "refusing: backup not found: $BACKUP" >&2
  exit 2
fi
BACKUP_DIR="$(dirname "$BACKUP_ABS")"
BACKUP_NAME="$(basename "$BACKUP_ABS")"
RESTORE_TOKEN=""

persist_version() {
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
  echo "RESTORE FAILED: writes remain closed. Check who holds the window:" >&2
  echo "  docker compose run --rm app python manage.py maintenance --check" >&2
  echo "If a failed update holds it, re-run with explicit audited takeover (writes stay closed):" >&2
  echo "  bash scripts/restore.sh $BACKUP $VERSION ${ALLOW_LOSS:+$ALLOW_LOSS }--takeover" >&2
  echo "Then check docker compose logs app. Only reopen writes after /readyz returns {\"ok\": true}." >&2
  if [[ -n "$RESTORE_TOKEN" ]]; then
    echo "This run holds token=$RESTORE_TOKEN; release only after readiness:" >&2
    echo "  docker compose exec -T app python manage.py maintenance --exit --token $RESTORE_TOKEN" >&2
  fi
}
trap 'fail_closed' ERR

echo "==> verifying backup integrity before touching live data"
docker compose run --rm -v "$BACKUP_DIR:/restore-src:ro" app \
  python manage.py verify_backup "/restore-src/$BACKUP_NAME"

echo "==> pulling matching release image"
docker pull "${IMAGE_BASE}:$VERSION"

echo "==> stopping writers, then restoring (restore_db holds maintenance)"
VERSION="$VERSION" IMAGE_BASE="$IMAGE_BASE" docker compose stop app
RESTORE_OUT="$(VERSION="$VERSION" IMAGE_BASE="$IMAGE_BASE" docker compose run --rm \
  -v "$BACKUP_DIR:/restore-src:ro" \
  -e APP_VERSION="$VERSION" app \
  python manage.py restore_db "/restore-src/$BACKUP_NAME" \
    --expected-version "$VERSION" $ALLOW_LOSS $FORCE)"
echo "$RESTORE_OUT"
RESTORE_TOKEN="$(echo "$RESTORE_OUT" | sed -n 's/.*maintenance-token: //p' | tr -d '[:space:]')"
if [[ -z "$RESTORE_TOKEN" ]]; then
  echo "refusing: restore did not report a maintenance token; writes stay closed" >&2
  echo "If the window is held by another operation (for example a failed update)," >&2
  echo "re-run with --takeover for an explicit audited takeover; writes stay closed." >&2
  exit 1
fi
VERSION="$VERSION" IMAGE_BASE="$IMAGE_BASE" docker compose up -d app
echo "==> readiness gate before reopening"
for i in $(seq 1 30); do
  if docker compose exec -T app python -c "import urllib.request; req = urllib.request.Request('http://127.0.0.1:8000/readyz/', headers={'X-Forwarded-Proto': 'https'}); print(urllib.request.urlopen(req, timeout=5).read().decode())" 2>/dev/null | grep -q '"ok": true'; then
    if docker compose exec -T app python -c "import urllib.request, json; req = urllib.request.Request('http://127.0.0.1:8000/readyz/', headers={'X-Forwarded-Proto': 'https'}); print(urllib.request.urlopen(req, timeout=5).read().decode())" 2>/dev/null | grep -q "\"version\": *\"$VERSION\""; then
      echo "restored database is serving version $VERSION"
      break
    fi
    echo "readiness reports wrong version; waiting for $VERSION" >&2
  fi
  if [[ "$i" == "30" ]]; then
    echo "readiness failed after restore; writes stay closed" >&2
    exit 1
  fi
  sleep 2
done
docker compose exec -T app python manage.py maintenance --exit --token "$RESTORE_TOKEN"
RESTORE_TOKEN=""
persist_version
trap - ERR
echo "restore complete: backup=$BACKUP_NAME version=$VERSION"
