"""Native Windows application server: Waitress serving Django on loopback.

Started by the "MTC Supplies - App" scheduled task (see install.ps1)::

    <root>\\current\\.venv\\Scripts\\python.exe <root>\\current\\deploy\\windows\\serve.py
        --env-file <root>\\settings.env --log-file <root>\\data\\logs\\app.log

Waitress is the pure-Python Windows replacement for Gunicorn. It runs as ONE
process with a thread pool, so exactly one process owns the SQLite database
(the same rule as the single Gunicorn tree in Docker). It listens on
127.0.0.1 only; Caddy terminates LAN HTTPS and forwards to it with
``X-Forwarded-Proto: https``.

``settings.env`` holds ``KEY=VALUE`` lines (rules in :func:`parse_env_text`,
mirrored by ``ConvertFrom-SettingsText`` in ``common.ps1``). Variables that
are already set in the process environment win over the file. Values are
never printed: the file holds the Django secret key.
"""

from __future__ import annotations

import argparse
import os
import re
import socket
import sys
from pathlib import Path

#: The release folder (``deploy/windows/serve.py`` -> release root).
RELEASE_ROOT = Path(__file__).resolve().parents[2]
LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = 8000
THREADS = 8
LOG_ROTATE_BYTES = 10 * 1024 * 1024
#: Alternative to --env-file (used when the argument is omitted).
ENV_FILE_VAR = "MTC_SETTINGS_ENV"

_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_BLANKS = " \t"


def parse_env_text(text: str) -> dict[str, str]:
    """Parse settings.env text into a dict.

    - one ``KEY=VALUE`` per line (``\\r\\n``, ``\\n`` or ``\\r`` endings);
    - a leading UTF-8 BOM is ignored;
    - blank lines and lines whose first non-blank character is ``#`` are
      ignored (there are no inline comments: ``#`` may appear in values);
    - spaces/tabs around the key and the value are trimmed;
    - one pair of matching surrounding quotes (``'...'`` or ``"..."``) is
      removed; there are no escapes and no variable expansion, so Windows
      paths keep their backslashes;
    - the last occurrence of a key wins (like ``tail -n 1`` in update.sh).

    A malformed line raises ``ValueError`` naming only the line number,
    never its content (it may hold a secret).
    """
    if text.startswith("﻿"):
        text = text[1:]
    values: dict[str, str] = {}
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for number, raw in enumerate(lines, start=1):
        line = raw.strip(_BLANKS)
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        key = key.strip(_BLANKS)
        if not sep or not _KEY_RE.match(key):
            raise ValueError(f"settings file line {number} is not KEY=VALUE")
        value = value.strip(_BLANKS)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def load_env_file(path: str | Path, environ=None) -> list[str]:
    """Apply settings.env to ``environ`` without overriding set variables.

    Returns the keys that were applied (never the values).
    """
    environ = os.environ if environ is None else environ
    text = Path(path).read_text(encoding="utf-8")
    applied = []
    for key, value in parse_env_text(text).items():
        if key in environ:
            continue
        environ[key] = value
        applied.append(key)
    return applied


def port_in_use(host: str = LISTEN_HOST, port: int = LISTEN_PORT,
                timeout: float = 1.0) -> bool:
    """True when something already accepts connections on host:port.

    Waitress sets SO_REUSEADDR, which on Windows lets a second server bind
    the same port and silently share connections; refuse instead, so two
    app processes (old and new code) never serve the same database.
    """
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def rotate_log(path: Path, max_bytes: int = LOG_ROTATE_BYTES) -> None:
    """Keep one previous log (``app.log.1``) once the log passes max_bytes."""
    try:
        if path.exists() and path.stat().st_size > max_bytes:
            os.replace(path, path.with_name(path.name + ".1"))
    except OSError:
        pass  # logging must never stop the server from starting


def server_options(threads: int = THREADS) -> dict:
    """Waitress settings: loopback only, one process, Caddy as the proxy."""
    return {
        "listen": f"{LISTEN_HOST}:{LISTEN_PORT}",
        "threads": threads,
        "ident": "mtc-supplies",
        # Waitress 3 strips ALL X-Forwarded-* headers unless the sender is a
        # trusted proxy. Only Caddy on this machine forwards, and only its
        # proto header is trusted; without this Django never sees HTTPS and
        # SECURE_SSL_REDIRECT would loop. X-Forwarded-Proto stays in the
        # environ for Django's SECURE_PROXY_SSL_HEADER.
        "trusted_proxy": LISTEN_HOST,
        "trusted_proxy_count": 1,
        "trusted_proxy_headers": {"x-forwarded-proto"},
        "clear_untrusted_proxy_headers": True,
        # Outer bound only; Django and the upload views enforce tighter caps.
        "max_request_body_size": 32 * 1024 * 1024,
    }


def _redirect_output(log_file: Path) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    rotate_log(log_file)
    stream = open(log_file, "a", encoding="utf-8", buffering=1,  # noqa: SIM115
                  errors="backslashreplace")
    sys.stdout = stream
    sys.stderr = stream


def _timestamp_logs() -> None:
    import logging

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    for handler in logging.getLogger().handlers:
        handler.setFormatter(formatter)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Serve the MTC Supplies Tracker with Waitress on 127.0.0.1:8000.")
    parser.add_argument("--env-file", default=os.environ.get(ENV_FILE_VAR, ""),
                        help=f"settings.env path (default: ${ENV_FILE_VAR})")
    parser.add_argument("--log-file", default="",
                        help="append stdout/stderr here (rotated at 10 MB)")
    parser.add_argument("--threads", type=int, default=THREADS)
    args = parser.parse_args(argv)

    if args.log_file:
        _redirect_output(Path(args.log_file))
    if not args.env_file:
        print(f"serve: pass --env-file or set {ENV_FILE_VAR}", file=sys.stderr)
        return 2
    try:
        load_env_file(args.env_file)
    except (OSError, ValueError) as exc:
        print(f"serve: cannot load settings file: {exc}", file=sys.stderr)
        return 2
    if port_in_use():
        print(f"serve: {LISTEN_HOST}:{LISTEN_PORT} is already in use; is the app "
              "already running? Not starting a second server.", file=sys.stderr)
        return 3

    if str(RELEASE_ROOT) not in sys.path:
        sys.path.insert(0, str(RELEASE_ROOT))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    # Imported lazily: only the native install needs waitress, and tests can
    # exercise everything above without it installed.
    from waitress import serve

    from config.wsgi import application
    from django.conf import settings

    _timestamp_logs()
    print(f"serve: MTC Supplies {settings.APP_VERSION or 'dev'} on "
          f"http://{LISTEN_HOST}:{LISTEN_PORT} (threads={args.threads})", flush=True)
    serve(application, **server_options(args.threads))
    return 0


if __name__ == "__main__":
    sys.exit(main())
