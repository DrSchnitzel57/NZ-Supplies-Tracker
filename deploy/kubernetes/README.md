# Kubernetes example (optional)

Example manifests for running the existing production image on a Kubernetes
cluster. They are **examples, not a tested deployment**: they were schema-
validated (kubeconform, Kubernetes 1.31, strict) but never applied to a real
cluster. Host names, image and sizes are synthetic placeholders.

## The one rule: exactly one pod

The app uses SQLite on a local disk. Exactly one pod may ever open the
database, so the Deployment is `replicas: 1` with `strategy: Recreate` (the old
pod is gone before the new one starts). Do not add an autoscaler, raise
`replicas`, switch to `RollingUpdate`, or put the volume on NFS/SMB/CephFS or
any `ReadWriteMany` share: SQLite locking is not safe there. Use block storage
(`local-path`, Longhorn, a cloud disk) with `ReadWriteOnce`.

| File | What it is |
| --- | --- |
| `namespace.yaml` | `mtc-supplies` namespace |
| `secret.example.yaml` | Shape of the Secret only - **create the real one with the command below** |
| `configmap.yaml` | `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`, `DJANGO_DB_PATH` on the volume |
| `pvc.yaml` | 5 GiB `ReadWriteOnce` volume for the database and its backups |
| `deployment.yaml` | The image, uid 10001 + `fsGroup`, probes on `/readyz/` and `/healthz/` |
| `service.yaml` | ClusterIP service on port 80 -> 8000 |
| `ingress.yaml` | TLS ends at the ingress (no Caddy needed); Traefik class, nginx notes inside |
| `kustomization.yaml` | Everything except the example Secret |

Written for a small k3s/k3d cluster (Traefik ingress, `local-path`
storage); other clusters need only the ingress class and storage class
changed. The app must be served at the root of its own host name (or its own
port): a path prefix such as `/supplies` is not supported without app
changes. Images from a local registry work the same way, e.g.
`k3d-registry.localhost:5000/supplies-tracker:X.Y.Z` (pin the tag; never
`:latest` with `rollout restart`).

## Install

1. Choose the host name staff will use and put it in `configmap.yaml`
   (`DJANGO_ALLOWED_HOSTS`, and `https://<host>` in
   `DJANGO_CSRF_TRUSTED_ORIGINS`) and `ingress.yaml` (both `host` fields).
   Pin the image tag in `deployment.yaml` and set `APP_VERSION` to the same
   value (`/readyz/` reports it).
2. Create the namespace, the app secret (random key) and the TLS secret:

   ```sh
   kubectl apply -f deploy/kubernetes/namespace.yaml
   kubectl -n mtc-supplies create secret generic supplies-tracker-secret \
     --from-literal=DJANGO_SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
   kubectl -n mtc-supplies create secret tls supplies-tracker-tls --cert=site.crt --key=site.key
   ```

   (Or let cert-manager issue `supplies-tracker-tls`.) A private registry also
   needs an image pull secret; see the comment in `deployment.yaml`.
3. Apply and prepare the database. The pod runs but stays *not ready*
   (`migrations-pending`) until `migrate` has run:

   ```sh
   kubectl apply -k deploy/kubernetes/
   kubectl -n mtc-supplies exec deploy/supplies-tracker -- python manage.py migrate --noinput
   kubectl -n mtc-supplies exec -it deploy/supplies-tracker -- python manage.py recover_admin --username admin --create
   ```

   `recover_admin` asks for the password interactively (hence `-it`); it is
   never passed on the command line.
4. Initial data (once, into the empty app). Build the bundle **outside** the
   cluster from a developer checkout (the private HTML must never go into an
   image), copy it in, import it:

   ```sh
   python manage.py seed_bundle --source "MTC Missionary Supplies Tracker.html" --output initial.zip
   POD=$(kubectl -n mtc-supplies get pod -l app.kubernetes.io/name=supplies-tracker -o name | head -1)
   kubectl -n mtc-supplies cp initial.zip "${POD#pod/}:/app/data/initial.zip"
   kubectl -n mtc-supplies exec deploy/supplies-tracker -- python manage.py import_bundle /app/data/initial.zip --username admin
   kubectl -n mtc-supplies exec deploy/supplies-tracker -- rm /app/data/initial.zip
   ```

## Settings that must match

- `DJANGO_ALLOWED_HOSTS` must contain the ingress host. Loopback and
  `localhost` are always allowed, which is why the probes send
  `Host: localhost` (the kubelet would otherwise send the pod IP and get 400).
- `DJANGO_CSRF_TRUSTED_ORIGINS` must be exactly `https://<host>` (plus
  `:port` if not 443), or every form post fails CSRF.
- The ingress must forward `X-Forwarded-Proto: https` (Traefik and ingress-nginx do).
  Django trusts that header for HTTPS redirects and secure cookies; the
  probes send it themselves because they bypass the ingress.

## Backups (honest version)

Backups must be taken by the one pod that owns the database, and they land
on the same volume (`/app/data/backups`). There is deliberately **no
CronJob**: a CronJob pod is a second process, and with a `ReadWriteOnce`
volume it may not even start on another node. Instead run the same commands
the Docker/Windows schedulers run, from outside, e.g. a host cron job or CI
schedule with cluster access:

```sh
kubectl -n mtc-supplies exec deploy/supplies-tracker -- python manage.py backup_db --quiesce --prefix daily-
kubectl -n mtc-supplies exec deploy/supplies-tracker -- python manage.py prune_backups --days 30
```

(A sidecar container in the same pod running that pair once a day is the
in-cluster alternative.) A backup on the same volume does not survive losing
the volume: copy backups off the cluster (`kubectl cp` of
`/app/data/backups/<name>.sqlite3` **and** its `.manifest.json`) or use volume
snapshots.

## Updates and restores

Same protocol as `scripts/update.sh`, by hand:

1. `exec ... python manage.py maintenance --enter --owner update-X.Y.Z --reason "update to X.Y.Z"`
   and note the printed token.
2. `exec ... python manage.py backup_db --dest /app/data/backups/pre-update-<stamp>.sqlite3 --quiesce --owner update-X.Y.Z --token <token>`
3. Change the image tag **and** `APP_VERSION` in `deployment.yaml`,
   `kubectl apply -k`, then `exec ... python manage.py migrate --noinput`.
4. Check `/readyz/` reports `"ok": true` and the new version, then
   `exec ... python manage.py maintenance --exit --token <token>`.

Restoring needs the app process stopped (like `scripts/restore.sh`): scale
the Deployment to 0, run a one-off pod from the **matching** image with the
same volume mounted to run `restore_db <backup> --expected-version X.Y.Z
--allow-newer-data-loss` (it prints a new maintenance token), scale back to
1, wait for readiness, then release the window with that token. This is not
scripted here.
