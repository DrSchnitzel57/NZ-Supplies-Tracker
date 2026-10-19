# Installing with Docker (Linux server, or a trial on any computer)

This is the container-based way to run the MTC Supplies Tracker. Use it on a
Linux server, a Kubernetes cluster (see `deploy/kubernetes/README.md`), or to
try the app on your own computer with invented data.

**Installing on a Windows PC in the office?** Use the simpler
[Windows guide](INSTALL_WINDOWS.md) instead — no Docker needed.

| What you want to do | Follow this section |
|---|---|
| Install an approved release for shared use | [Deploy a published image](#deploy-a-published-image) |
| Build the application yourself and try it with invented data | [Build from source](#build-from-source) |

Use **one** path. The source trial is not a live shared installation. Neither
path loads a real roster automatically.

## Before you start

- **Docker** runs the application in its own package, called an *image*.
  **Docker Compose** starts the application and its secure web connection together.
  You do not need to learn Docker commands beyond the ones shown here.
- Install Docker on the **hosting computer only**. For Windows/macOS local trials,
  use [Docker Desktop](https://www.docker.com/products/docker-desktop/), follow its
  installer and open it after installation. Your organisation must check licensing
  and allow the installation. For a Linux host, follow Docker's
  [Engine installation guide](https://docs.docker.com/engine/install/) and
  [Compose plugin guide](https://docs.docker.com/compose/install/linux/), or ask
  someone to help. Linux is the tested shared-host setup; Windows/macOS host
  networking, reboot behaviour and scheduled backups need separate verification.
- Keep the application folder and Docker's data on the computer's **local disk**,
  not OneDrive or a network drive. Allow space for the application and backups.
- Installation needs internet access. Once installed, routine use needs only
  access to the hosting computer. It must remain on and connected; sleep/shutdown
  makes the application unavailable.

### Open a command window in the application folder

Get the application ZIP/folder from the maintainer. For a published release,
use the folder that matches that release. The [repository](https://github.com/DrSchnitzel57/NZ-Supplies-Tracker)
is public: downloading its source needs no GitHub account. Select the supplied
release/tag, then use **Code → Download ZIP**. Extract it first; do not work
inside the ZIP preview.

Open the extracted folder containing `README.md`, `compose.yaml`,
`compose.test.yaml`, `.env.example` and `Dockerfile`.
Files beginning with a dot may be hidden: in macOS Finder press **Command +
Shift + .**; in Linux file managers try **Ctrl + H**. On Windows, enable
**View → Show → Hidden items** if needed.

- **Windows:** in File Explorer, open that folder, click its address bar,
  type `powershell`, then press Enter.
- **macOS:** open Terminal using Spotlight. Type `cd ` (including the space),
  drag the extracted folder into the window, then press Enter.
- **Linux:** open the folder in the file manager and choose **Open in Terminal**,
  if available.

Copy each command below into that window and press Enter. Run **one at a time**;
wait for the prompt to return before running the next. Do not type the Markdown
backticks. Stop if a command reports an error; see [Troubleshooting](#troubleshooting).

Check that Docker is installed:

```sh
docker --version
```

```sh
docker compose version
```

Both should print a version. Docker Desktop must also be running, or on Linux
the Docker service must be running. You do not need Python installed for either
Docker installation path below.

## Deploy a published image

This downloads an already-built, approved release. **You do not build source**
or run developer tests on the live host.
Public release images need **no GitHub account, download token or Docker login**.
Public downloads do not make your running installation public: keep it LAN-only,
and keep its database, backups and `.env` private.

### 1. Obtain the release details

Ask the maintainer for:

- The **image address**, without a version at the end.
- The **release version** and matching application folder.
- The hosting computer's **fixed local-network address**, and confirmation that
  its firewall will allow staff access on port `8443` only from the approved LAN.

Do not open a router port or expose this application to the public internet.
If you do not know the fixed address or firewall settings, get setup assistance.

**A release must actually have been published first.** The image/version in
`.env.example` are placeholders, not proof that a downloadable release exists.
Use `ghcr.io/drschnitzel57/nz-supplies-tracker` for `IMAGE_BASE` once the maintainer
confirms a public release there, and use the exact version they supply.
If downloading is denied, ask the maintainer to check the package is public;
you should not need their publishing credentials.

### 2. Set the installation details

In the application folder, make a copy of `.env.example` named exactly **`.env`**.
Edit it in a plain-text editor. On Windows, enable **View → Show → File name
extensions** so it does not accidentally become `.env.txt`.

Change these lines to match the details you were given:

| Setting | What to enter |
|---|---|
| `IMAGE_BASE` | The supplied image address, with **no** `:version` suffix |
| `VERSION` | The supplied release version; never `latest` |
| `APP_HOST` | The hosting computer's fixed LAN address |
| `APP_PORT` | `8443`, unless a different port was agreed |
| `DJANGO_ALLOWED_HOSTS` | The same address as `APP_HOST`, without `https://` or a port |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | `https://` followed by that address and port, for example `https://192.168.1.10:8443` |

Leave `DJANGO_DEBUG=0` and `DJANGO_DB_PATH=/app/data/db.sqlite3` unchanged.
`192.168.1.10` is only an example; use the address assigned to your host.

Save the file, then download the release:

```sh
docker compose pull
```

Generate a private application key (this does not start the website):

```sh
docker compose run --rm --no-deps app python -c "import secrets; print(secrets.token_hex(32))"
```

Copy the printed 64-character value into `.env` after `DJANGO_SECRET_KEY=`,
replacing the placeholder, and save. Keep `.env` private. Generate this key
**once for a new installation**; do not change it on routine restarts/updates.

### 3. Start the application

```sh
docker compose up -d
```

Create the empty database tables:

```sh
docker compose exec app python manage.py migrate --noinput
```

Successful output ends with completed migrations marked `OK`, or says there are
no migrations to apply. Check the running services:

```sh
docker compose ps
```

Both `app` and `caddy` should be running. `app` may briefly report `starting` or
`unhealthy` before migrations and its next health check; allow about a minute.
If it remains unhealthy, do not continue with real data.

Open `https://YOUR-HOST-ADDRESS:8443/readyz/` in a browser, replacing the address
and port with yours. Expect a small response containing **`"ok": true`** and
your release version. Then open the same address without `/readyz/` to sign in.

**Certificate warning:** the application uses a locally issued certificate.
Check the address with the person setting up the host. Only continue through a
browser's **Advanced / Continue** option for this specific, verified installation
if your organisation allows it; do not disable browser protection globally.
If the browser does not allow continuing, ask for help. Certificate trust setup
is separate; it is not performed by these steps.

### 4. Create the first administrator

Run this **once** on a new installation:

```sh
docker compose exec app python manage.py recover_admin --username tracker-admin --create
```

Choose a temporary password and enter it twice when prompted. No characters
may appear while typing; this is normal. Expect `Created administrator`.
Running the command again resets that account's password and signs it out.

Sign in in the browser as `tracker-admin` with that temporary password. The
application asks you to choose a new password before proceeding to Overview.

Before real use, complete the [sample test](#try-a-complete-workflow) with invented
data. Do not later mix trial records into a real roster: arrange a separate empty
installation for real data, or have the administrator/operator prepare it safely.

### 5. Prepare for shared use

- In **Accounts**, create individual staff accounts. Leave **Administrator**
  unchecked for ordinary staff. If asked, confirm your own password first, then
  return and submit the account form. Each person changes their temporary password
  on first sign-in. Give staff the verified browser address, not Docker commands.
- Have the operator configure daily backups, check that they succeed, and perform
  a restore/reboot test using [Operations](OPERATIONS.md). **Starting the app
  does not schedule daily backups.** Backups kept on this host cannot protect
  against losing the host or its disk.
- Real-source import/fidelity, unaided staff testing and actual-host network,
  registry, reboot and recovery checks still need completion before rollout.
- Import approved real data only into the intended installation. Routine imports
  live in **Missionaries** and **Inventory**; administrator-only full ZIP import
  lives in **Migration and full bundles** and requires empty operational data.
  See [Data format](DATA_FORMAT.md). No initial data is loaded automatically.

Stop the website without deleting its data:

```sh
docker compose stop
```

Start it again:

```sh
docker compose start
```

Keep the same folder and installation settings. Do not delete Docker volumes or
use `down -v`; that deletes stored data. Do not simply change the image version
to update a live installation: use the controlled update/backup procedure in
[Operations](OPERATIONS.md).

## Build from source

This alternative builds the code on your computer and runs a **local trial**.
It is not the deployment path above. Use invented data only: the trial uses
publicly known test settings, has no scheduled backups and is reachable only
from this computer. Do not use it as the shared live application.

Follow [Before you start](#before-you-start) to install/start Docker, extract the
source ZIP and open a command window in the application folder. The private HTML
prototype is **not needed**. No registry download token or `.env` setup is needed
for this path.

Build and start the trial (the first build may take several minutes):

```sh
docker compose -p supplies-demo -f compose.test.yaml up -d --build
```

Create its database tables:

```sh
docker compose -p supplies-demo -f compose.test.yaml exec app python manage.py migrate --noinput
```

Create its administrator **once**:

```sh
docker compose -p supplies-demo -f compose.test.yaml exec app python manage.py recover_admin --username demo-admin --create
```

Enter a temporary password twice. Open **https://127.0.0.1:18443/readyz/** and
expect `"ok": true`. For this trial, the version is `0.0.0-test`, not a published
release version. The local-certificate warning described above may appear;
`127.0.0.1` refers only to your own computer.

Then open **https://127.0.0.1:18443/**. Sign in as `demo-admin` and change the
temporary password when asked. Overview should show an empty roster/inventory.
Follow the sample below to try the app.

Stop the trial without deleting its records:

```sh
docker compose -p supplies-demo -f compose.test.yaml stop
```

Resume that same trial:

```sh
docker compose -p supplies-demo -f compose.test.yaml start
```

Its records are in Docker's `supplies-demo` data volumes, not in the downloaded
code files. Keep the same project name/folder; do not delete its volumes if you
want to retain the trial. Do not share this trial address with other computers.

## Try a complete workflow

**Fastest:** fill an empty trial with invented missionaries, stock and three
weeks of history, then explore:

```sh
docker compose -p supplies-demo -f compose.test.yaml exec app python manage.py load_demo_data --username demo-admin
```

The command refuses to run if the installation already has any missionary, item
or issue, so it can never be mixed into a real roster.

**By hand:** use these **invented** examples on an empty trial installation:

1. **Missionaries → Add a missionary:** Title `Sister`, Name `Demo, Example`,
   Missionary ID `DEMO-001`. Leave the rest blank and save. She is shown to
   staff as **Sister Demo**.
2. **Inventory → Add an item:** Category `Hygiene`, Item name `Soap`, Opening
   quantity `10`, Low-stock alert level `2`. Save; **Available** shows **10**.
3. **Issue supplies:** type `Demo` and choose **Sister Demo**. Press **Add** on
   Soap, set the quantity to `2` and choose **Issue supplies**. Inventory now
   shows **8**.
4. **Inventory → Add stock** on Soap: quantity `3`, reason `Demo delivery`.
   Available is **11**.
5. **History:** open the issue from step 3, give the reason `Demo undo test` and
   choose **Undo this issue**. Available is **13**; the issue stays in history
   marked **Undone**.
6. **Reports:** choose **Stock on hand now** and **Show report**. Download the
   CSV or PDF and check Soap shows **13**. A given-out summary excludes the
   undone issue.

For everyday work, use the illustrated [Quick-start guide](QUICK_START.md),
also linked inside the app. Imports preview changes before applying them;
blank/missing optional cells preserve existing values. Use **Edit** to clear a
field deliberately. Full reset is a protected destructive admin action, not a
normal troubleshooting step.

**Unfinished work:** drafts last only in the open tab. Closing/reloading discards
them. Sign-in expires after 30 minutes without activity; automatic stock refreshes
do not keep you signed in. After expiry, keep the draft tab open, sign in in another tab
as the **same person**, then retry here. If a reply is uncertain, retry the original
request or inspect its history in another tab; do not create a duplicate delivery
or issue. Other stock forms are blocked until that uncertain request is resolved.

## Troubleshooting

| What you see | What to do |
|---|---|
| `docker` is not recognised / command not found | Install Docker, then reopen the command window. |
| Cannot connect to the Docker daemon | Open Docker Desktop and wait until it is running; on Linux ask the operator to check the Docker service/permissions. |
| No configuration file / cannot find `compose.test.yaml` | Open the extracted folder containing this README, then open the command window there. |
| Download denied / image or manifest not found | Ask the maintainer to confirm the release exists at the supplied address/version and its package is public. No download token is needed for public images. Do not guess `latest`. |
| Port already allocated / address already in use | Another application/trial may be using the port. Ask for help; do not stop unfamiliar services or disable the firewall. |
| Cannot assign requested address | `APP_HOST` must belong to this computer. Confirm its fixed LAN address. |
| Website does not open | Check Docker is running, the hosting computer is awake, and you used the exact HTTPS address/port for your chosen path. |
| Sign-in rejects the password | Check the username and exact password (spaces count). For the trial administrator, rerun its `recover_admin` command only if you intend to reset that account. |
| “Stale count” | Stock changed after you opened the form. Review current stock and recount; do not blindly submit your old number. |

For diagnostic status, use `docker compose ps` for a published deployment or
`docker compose -p supplies-demo -f compose.test.yaml ps` for the source trial.
Record the failing step and error text for support, but never send passwords,
tokens, `.env`, private roster data or database files in an ordinary support message.

## Technical references and automated tests

- [Operations: backups, account recovery, updates and restore](OPERATIONS.md)
- [Import/export data format](DATA_FORMAT.md)
- [Architecture](ARCHITECTURE.md) and [roadmap](history/ROADMAP.md)
- [Review evidence and remaining manual gates](history/REVIEW.md)

Passing automated tests is not permission to import real data or deploy:
the host, staff and source checks in [Operations](OPERATIONS.md) still apply.

<details>
<summary>Developers: run automated checks from source (Linux/macOS commands)</summary>

This is optional developer work, **not needed to install/use a published image**.
Install Python 3.13 and [uv](https://docs.astral.sh/uv/getting-started/installation/).
Keep Docker running for deployment tests. Stop any local trial first: those tests
also need port `18443`. Run suites serially; Django/browser tests share a test DB.

```sh
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -r requirements.txt
mkdir -p .local
.venv/bin/python -m playwright install chromium
.venv/bin/python manage.py check
.venv/bin/python manage.py makemigrations --check --dry-run
.venv/bin/python manage.py test --noinput
.venv/bin/python -m pytest tests/browser
.venv/bin/python -m pytest tests/deployment
git diff --check
```

Run each line separately. Tests use synthetic data only. Browser tests require
Chromium and fail rather than skip if it is missing; all internet requests are
blocked during app browser testing. PDF tests check actual text/fonts/page bounds
and rendered pages. Production images include runtime dependencies only, not
pytest/Playwright/the native PDF test renderer.

</details>
