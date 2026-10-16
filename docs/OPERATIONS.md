# Operations — host install, backups, updates, rollback

> **On Windows without Docker?** [INSTALL_WINDOWS.md](INSTALL_WINDOWS.md) covers
> install, backups, updates, rollback and recovery with the `mtc` command. This
> page is the Docker/Linux reference; the Kubernetes example is in
> [deploy/kubernetes](../deploy/kubernetes/README.md).

Single-host LAN stopgap. The operator owns installation, updates, backup
monitoring and host/network recovery; staff and administrators use the
browser UI for accounts and daily work. All examples use synthetic values.

## Host prerequisites

- Docker Engine + Compose plugin on the host (Linux expected; Windows/macOS
  Docker operation and licensing need operator verification).
- Fixed LAN address for the host (example `192.168.1.10`), port `8443`.
- Outbound internet for release pulls only; routine work needs only the LAN.
- Host disk with room for the database plus 30+ days of backups.

## Firewall / IP / port

- Bind is fixed: `APP_HOST:APP_PORT` in `.env` (example `192.168.1.10:8443`).
- Firewall must allow LAN clients to reach that IP/port and deny everything
  else. No router port forwarding; no public exposure.
- Caddy serves HTTPS only on that address with a locally issued certificate.
  Browsers warn until they trust the local CA. Staff PCs can download it from
  `https://APP_HOST:APP_PORT/ca.crt` and add it to *Trusted Root Certification
  Authorities* (steps in [INSTALL_WINDOWS.md](INSTALL_WINDOWS.md#let-staff-pcs-trust-the-connection);
  check the thumbprint, or push it by Group Policy). HTTPS is still required:
  a self-issued certificate alone does not establish LAN isolation.

## Secrets and bootstrap

1. Copy `.env.example` to `.env`; set `DJANGO_SECRET_KEY` (long random),
   `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`, `APP_HOST`,
   `APP_PORT`, `IMAGE_BASE`, `VERSION`. `IMAGE_BASE` names the repository
   WITHOUT a tag (legacy `IMAGE` is honored as a fallback but must also be
   tagless); `VERSION` is the single pin and the running image is always
   `IMAGE_BASE:VERSION` with `APP_VERSION` equal to `VERSION`.
2. Pull and start the pinned release (host never builds source):
   `docker compose pull && docker compose up -d && docker compose exec app python manage.py migrate`
3. Check readiness: app `/readyz` returns `{"ok": true}`; `/healthz` is the
   liveness ping. Both carry no personal data or secrets.
4. Bootstrap admin: `docker compose exec app python manage.py recover_admin --username NAME --create`
   (interactive password prompt; never passed as an argument or logged).
5. Import the original HTML tracker once into empty data (both commands print
   counts only, never names):
   `docker compose cp "MTC Missionary Supplies Tracker.html" app:/app/data/original.html`, then
   `docker compose exec app python manage.py seed_bundle --source /app/data/original.html --output /app/data/initial.zip`, then
   `docker compose exec app python manage.py import_bundle /app/data/initial.zip --username ADMIN`.
   Delete `original.html` from the volume afterwards if it is no longer needed. For a trial with invented data instead, use
   `python manage.py load_demo_data --username ADMIN` (refuses non-empty data).
   Full bundles are admin-only and import only into empty operational data.

## Account recovery

- Administrators create/reset accounts in the UI; users must change temporary
  passwords before access. Deactivation signs sessions out immediately.
- Lost admin: use `recover_admin` on the host as above.

## Backups (host-local, 30-day retention)

- Verified SQLite backups via the backup API (never a naive WAL file copy):
  `docker compose exec app python manage.py backup_db --dest /app/data/backups/manual-NAME.sqlite3 --quiesce`
  (`--quiesce` holds an owned maintenance window during the copy).
- Every backup is atomic (temporary file + fsync + rename) and records a
  `<name>.manifest.json` sidecar (path, checksum, app version, schema head,
  completion). Only a completed backup with a matching manifest counts as
  verified; interrupted copies can never pass verification.
- Daily scheduler: install `scripts/daily_backup.sh` as a daily host cron
  job; it also runs `prune_backups --days 30`. The newest **verified**
  `pre-update-*`/`pre-reset-*` recovery backup is protected until superseded
  by a newer verified one — corrupt files are skipped and can never become
  the protected backup.
- Artifacts are permission-restricted (0600 files, 0700 dirs); verification
  refuses backups with unsafe permissions. Restored/copied files keep 0600:
  after copying a backup onto the host, run `chmod 600` before verifying.
- Verify with `docker compose exec app python manage.py verify_backup PATH`
  (initialized Django entrypoint; never a bare `python -c` without settings,
  which cannot run the schema/release checks).
- Monitor backup age/size; a failed backup must block reset/update (fail
  closed) and never report silent success.
- Crashed operations keep writes closed (fail closed): inspect with
  `python manage.py maintenance --check`; take over explicitly with
  `python manage.py maintenance --enter --force --owner NAME` (audited), and
  release with the printed `--token`.
- Limitation: host-only backups cannot survive disk/host loss. Copy the
  backups folder (each `.sqlite3` with its `.manifest.json`) to another
  machine or drive regularly.

## Updates (controlled, brief interruption)

- `bash scripts/update.sh X.Y.Z [--takeover]`: validates config/release (the requested pin
  always wins over `.env`; a tag inside `IMAGE_BASE`/`IMAGE` is refused),
  enters an owned maintenance window (a held
  window aborts the update instead of sharing it; `--takeover` takes over
  explicitly and audited after a crash), takes a verified backup
  to an exact file, pulls `IMAGE_BASE:X.Y.Z`, migrates, restarts, checks
  readiness including the serving `APP_VERSION`, durably records the new
  `VERSION` in `.env` (atomic, only on success, so later plain Compose runs
  the same release), then exits maintenance with its token.
- Coordinates other writers and operator runs via the owned maintenance
  window; concurrent writes fail with a clear maintenance message, not
  corruption. Restore order is always verify → restore → migrate →
  readiness → reopen writes.
- On failure the script keeps mutation access closed and prints exact
  recovery steps (inspect logs, restore recovery backup with `--takeover`,
  retry update with `--takeover`, owner/token, audited force-takeover
  command) plus the
  run's maintenance token for a deliberate release after recovery.
- No unattended `latest` upgrades; no zero-downtime promise.

## Rollback

- `bash scripts/restore.sh BACKUP X.Y.Z [--allow-newer-data-loss] [--takeover]`: verifies
  the backup via `verify_backup` (integrity + schema + release), pulls the matching image, stops
  writers, restores under exclusive maintenance, re-acquires the window
  (restoring replaces the maintenance row; `--takeover` takes over a window
  held by a failed update explicitly and audited, writes stay closed),
  migrates, rechecks readiness including `APP_VERSION`, persists `VERSION`
  in `.env`, then releases the printed maintenance token — only after the script's own
  readiness gate. Without the flag, restore refuses when live data holds
  newer work or when another operation holds the window. Pre-write refusals reopen the window; post-restore failures
  keep it held and print the token for deliberate recovery. The takeover is
  re-recorded after the file swap so the audit survives the restore.
- Image rollback alone cannot undo an incompatible migration; always pair
  the image with its compatible database backup.

## Host-loss limitation

Backups live on the host. Host/disk loss destroys live data and backups
together unless someone copies the backups folder elsewhere; the app does not
do that by itself.
