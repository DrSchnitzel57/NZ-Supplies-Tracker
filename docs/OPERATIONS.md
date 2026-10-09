# Operations — host install, backups, updates, rollback

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
  Browsers warn unless they trust the local CA. Certificate trust setup and
  distribution are out of scope (see Risks); HTTPS is still required because
  a self-issued certificate alone does not establish LAN isolation.

## Secrets and bootstrap

1. Copy `.env.example` to `.env`; set `DJANGO_SECRET_KEY` (long random),
   `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`, `APP_HOST`,
   `APP_PORT`, `IMAGE`, `VERSION`.
2. Pull and start the pinned release (host never builds source):
   `docker compose pull && docker compose up -d && docker compose exec app python manage.py migrate`
3. Check readiness: app `/readyz` returns `{"ok": true}`; `/healthz` is the
   liveness ping. Both carry no personal data or secrets.
4. Bootstrap admin: `docker compose exec app python manage.py recover_admin --username NAME --create`
   (interactive password prompt; never passed as an argument or logged).
5. Import the initial bundle once into empty data:
   `docker compose exec app python manage.py import_bundle /path/initial.zip --username ADMIN`
   Full bundles are admin-only and import only into empty operational data.

## Account recovery

- Administrators create/reset accounts in the UI; users must change temporary
  passwords before access. Deactivation signs sessions out immediately.
- Lost admin: use `recover_admin` on the host as above.

## Backups (host-local, 30-day retention)

- Verified SQLite backups via the backup API (never a naive WAL file copy):
  `docker compose exec app python manage.py backup_db`
- Daily scheduler: install `scripts/daily_backup.sh` as a daily host cron
  job; it also runs `prune_backups --days 30`. The newest verified
  `pre-update-*` recovery backup is protected until superseded by a newer one.
- Artifacts are permission-restricted (0600/0700). Verify with
  `python manage.py shell -c "from apps.operations import backup; backup.verify_sqlite_backup('PATH')"`.
- Monitor backup age/size; a failed backup must block reset/update (fail
  closed) and never report silent success.
- Limitation: host-only backups cannot survive disk/host loss. Keep a
  separate copy procedure if that risk must be covered (out of scope).

## Updates (controlled, brief interruption)

- `bash scripts/update.sh X.Y.Z`: validates config/release, enters
  maintenance (writes refused), takes a verified backup, pulls the pinned
  image, migrates, restarts, checks readiness, then exits maintenance.
- Coordinates other writers and operator runs via the maintenance flag;
  concurrent writes fail with a clear maintenance message, not corruption.
- On failure the script keeps mutation access closed and prints exact
  recovery steps (inspect logs, restore recovery backup, re-run).
- No unattended `latest` upgrades; no zero-downtime promise.

## Rollback

- `bash scripts/restore.sh BACKUP X.Y.Z [--allow-newer-data-loss]`: verifies
  the backup (integrity + schema), pulls the matching image, restores under
  exclusive maintenance, migrates and rechecks readiness.
- Without the flag, restore refuses when live data holds newer work.
- Image rollback alone cannot undo an incompatible migration; always pair
  the image with its compatible database backup.

## Host-loss limitation

Backups live on the host. Host/disk loss destroys live data and backups
together. There are no external backups in this release by design.
