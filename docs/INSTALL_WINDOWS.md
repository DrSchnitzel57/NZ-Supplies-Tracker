# Installing the MTC Supplies Tracker on Windows

This guide puts the Supplies Tracker on one Windows PC in the office. Staff
then use it from their own computers through a web browser. No Docker, no
accounts with outside services, nothing to pay for.

The PC that runs it is called **the server** below. Everything here is done
on the server unless it says "staff PC".

---

## What you need

- A Windows 10, Windows 11 or Windows Server PC that **stays switched on**
  during work hours (set *Sleep* to *Never* in Power settings).
- An account with **administrator** rights on it.
- **Internet** during installation and updates (not for daily use).
- A **fixed address** for the server on the office network. Ask IT to
  "reserve" its IP address (a DHCP reservation); otherwise the address can
  change and staff lose the link.
- About 2 GB of free disk space, plus room for 30 days of backups.
- The release file, for example `mtc-supplies-1.0.0.zip`.

## Install (about 10 minutes)

1. Copy the release `.zip` onto the server's own disk (for example the
   Desktop - not a network drive).
2. Right-click it, choose **Extract All...**, then **Extract**.
3. In the extracted folder open `deploy`, then `windows`, and double-click
   **`install.cmd`**. Click **Yes** when Windows asks for permission.
4. Answer the few questions:
   - **The address staff will use** - it suggests one, like
     `https://192.168.1.10:8443`. Press Enter if it looks right.
   - **A username for the first administrator** (for example `admin`) and a
     **temporary password**, typed twice. Nothing shows while you type; that
     is normal. You change it at the first sign-in.
   - **Trust the certificate on this PC?** Answer yes.
5. At the end it prints a green summary with the staff address, where the
   backups are and the next steps. Keep that window open until you've read it.

If anything goes wrong it says so in plain words. Fix the problem (usually
the internet connection) and double-click `install.cmd` again; it carries on
where it stopped.

Everything lives in **`C:\MTCSupplies`**. Only administrators can open that
folder; that is deliberate (it holds the database and the secret key).

> For IT: `install.ps1` takes `-Root D:\Folder`, `-Port 9443`,
> `-HostAddress 192.168.1.10` (or a computer name) and `-Yes`. Run it from an
> administrator PowerShell with `powershell -ExecutionPolicy Bypass -File install.ps1 ...`.

## First sign-in

1. On the server, open **https://192.168.1.10:8443** (your address) in Edge
   or Chrome.
2. Sign in with the administrator name and temporary password, then choose a
   new password.
3. Create accounts for the staff in the admin area.

## Bring in the original tracker's data

Do this **once, before anyone starts entering data** (it only works while the
app is empty). Put the original `MTC Missionary Supplies Tracker.html` on the
server, then in an **administrator Command Prompt** (Start, type `cmd`,
right-click *Command Prompt*, *Run as administrator*):

```
C:\MTCSupplies\mtc import-original "C:\Users\me\Desktop\MTC Missionary Supplies Tracker.html" -Username admin
```

It shows only counts (people, items), never names. The HTML file contains
personal details: keep it somewhere private or delete it afterwards.

## Let staff PCs trust the connection

The app uses its own certificate. Until a staff PC trusts it, the browser
shows a "Your connection isn't private" warning. Do this once per staff PC:

1. On the staff PC open **https://192.168.1.10:8443/ca.crt** (your address).
   The first time the browser warns; choose *Advanced*, then *Continue*. A file
   `MTC-Supplies-CA.crt` downloads.
2. Open the downloaded file, click **Install Certificate...**
3. Choose **Local Machine**, then **Next** (allow the permission prompt).
4. Choose **Place all certificates in the following store**, **Browse...**,
   pick **Trusted Root Certification Authorities**, **OK**, **Next**, **Finish**.
5. Close and reopen the browser. The warning is gone.

To be sure it is the right certificate, the file's *Details* tab shows a
**Thumbprint**; it must match the one `mtc status` prints on the server.

**Many PCs?** IT can push the file to every PC with Group Policy: *Computer
Configuration > Policies > Windows Settings > Security Settings > Public Key
Policies > Trusted Root Certification Authorities > Import*, using
`C:\MTCSupplies\MTC-Supplies-CA.crt`. Edge and Chrome use the Windows list;
if Firefox still warns, import the file in Firefox's own certificate settings.

## Everyday administrator commands

Open an **administrator Command Prompt** and type `C:\MTCSupplies\mtc` and a
command. (`C:\MTCSupplies\mtc help` lists them all.)

| Command | What it does |
| --- | --- |
| `mtc status` | Is it running, which version, last backup, free disk space |
| `mtc create-admin NAME` | Make another administrator |
| `mtc recover-admin NAME` | Forgotten administrator password: set a temporary one |
| `mtc backup` | Take a backup right now |
| `mtc restart` | Restart the app (also fixes most "not answering" problems) |
| `mtc logs` | Show the latest log lines (useful for the developer) |

## Backups

- Every night at **02:00** the server saves a checked copy of the database to
  **`C:\MTCSupplies\data\backups`** and deletes copies older than **30 days**.
  The most recent "before update" copy is always kept.
- `mtc status` shows the latest backup; it turns yellow if the last one is more
  than a day old.
- These backups are on the same PC. **Copy the backups folder somewhere else
  regularly** (a USB drive or a network folder), so a broken disk or a lost
  PC does not take everything with it.
- To put a backup back: `mtc restore "C:\MTCSupplies\data\backups\daily-....sqlite3"`.
  You must type `RESTORE`; a safety copy of the current data is kept.

## Updating to a new version

1. Copy the new release `.zip` to the server, **Extract All**.
2. In the new folder open `deploy\windows` and double-click **`update.cmd`**.
   (Or: `C:\MTCSupplies\mtc update "C:\path\mtc-supplies-1.0.1.zip"`.)

While it runs, staff can still look things up but **saving is paused for a
few minutes** and the app restarts once. It saves a backup first. If anything
goes wrong it **puts the old version and the data back by itself** and says
so. The last three versions stay on disk.

## Going back after an update (rollback)

If the new version misbehaves:

```
C:\MTCSupplies\mtc rollback
```

It goes back to the previous version **and** to the data as it was just
before that update. It tells you when that was; anything entered since then
will no longer be in the app (a safety copy is kept). Type `RESTORE` to go
ahead. A rollback can be done once; to move forward again, run the update
again.

## Troubleshooting

| Problem | What to do |
| --- | --- |
| Staff PCs cannot open the address at all | On the server run `mtc status`. If the address shown is not the server's current address, run `mtc repair -HostAddress <new address>`. Make sure the office network is set to *Private* in Windows (the installer offers to fix this). |
| "Your connection isn't private" on a staff PC | That PC does not trust the certificate yet: see *Let staff PCs trust the connection*. (After `mtc repair` with a new address the same certificate stays valid; staff just use the new address.) |
| "Bad Request (400)" | The address typed is not the one the app was set up with. Use the address from `mtc status`, or `mtc repair -HostAddress ...`. |
| Staff are told saving is paused / maintenance | An update or backup is running; wait a few minutes. If it stays like that, run `mtc status`. After an interrupted update run the update again with `-Takeover`, or `mtc rollback -Takeover`. |
| Nothing answers after a restart of the server | Give it two minutes, then `mtc status`, `mtc restart`, `mtc logs`. |
| Install or update stops while downloading | Check the internet connection. Behind a proxy, IT must set `HTTPS_PROXY` first. If the network inspects HTTPS, run the install/update again with `-UseSystemCertificates`. |
| "Running scripts is disabled on this system" | Use `install.cmd`, `update.cmd` and `mtc`, which allow the scripts for that one run. |
| Backups failing (`mtc status` yellow) | `mtc logs` shows why; usually disk space. |
| Moving to a new server | Install on the new PC, copy a backup (the `.sqlite3` **and** its `.manifest.json`) across, then `mtc restore <file>` there. Staff PCs must then trust the new server's certificate. |

## Uninstalling

```
C:\MTCSupplies\mtc uninstall
```

removes the scheduled tasks, firewall rule and program, but **keeps the data**
(`C:\MTCSupplies\data`, `settings.env`). Installing again into the same folder
picks it all up. To delete everything, including every backup, add
`-DeleteData` and type `DELETE` when asked - copy the backups elsewhere first.

---

## For the developer

**Release zip.** Bump `VERSION` (X.Y.Z), commit, then
`git archive --format=zip --prefix=mtc-supplies-X.Y.Z/ -o mtc-supplies-X.Y.Z.zip HEAD`.
`git archive` leaves out ignored files, so the private HTML, databases and
`.env` never end up in it, and it writes the `.cmd` files with Windows line
endings (`deploy/windows/.gitattributes`). Releases must ship wheels-only
dependencies (`uv pip install --only-binary :all:`).

**Layout.**

```
C:\MTCSupplies\
  releases\<version>-<yyyyMMdd-HHmmss>\   app code + its own .venv + collected static
  current\                                 junction -> active release
  previous.txt                             release to go back to (rollback)
  data\db.sqlite3, data\backups\, data\logs\, data\imports\
  data\caddy\                              Caddy data (XDG_DATA_HOME), incl. the local CA
  settings.env                             KEY=VALUE settings incl. the secret key
  tools\uv.exe, tools\caddy.exe, tools\python\
  Caddyfile, MTC-Supplies-CA.crt, mtc.cmd
```

**Moving parts.** Scheduled tasks run as SYSTEM: *MTC Supplies - App*
(`deploy/windows/serve.py`: Waitress on 127.0.0.1:8000, one process),
*MTC Supplies - HTTPS* (Caddy via `run-caddy.cmd`) and *MTC Supplies - Daily
backup* (`backup.ps1`). Both services start at boot and are restarted within a
minute if they stop. The firewall rule allows the HTTPS port on private and
domain networks only. Pinned tools: uv 0.12.20 (SHA-256) and Caddy 2.10.2
(SHA-512), each checked against the hash in `common.ps1` **and** the
release's published checksum list. `APP_VERSION` comes from each release's
`VERSION` file; do not set it in `settings.env`. Update, rollback and restore
use the same maintenance-window/token protocol as `scripts/update.sh` and
`scripts/restore.sh`; logs for each are in `data\logs`. Updates do not touch
the generated `Caddyfile`, tasks or firewall rule: if a release changes those
templates, run `mtc repair` after updating.
