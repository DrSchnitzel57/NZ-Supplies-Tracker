"""Native Windows install support, exercised on any OS (synthetic data only).

- The backup/restore/verify/prune code's Windows branches run on POSIX by
  patching ``apps.operations.backup.IS_WINDOWS`` (and simulating Windows
  file-mode semantics), while the POSIX behaviour stays strict.
- ``deploy/windows/serve.py`` (the Waitress launcher) is loaded from its file
  and tested without waitress installed (a stub module records the call).
- The PowerShell scripts are parsed, scanned for PowerShell-7-only syntax and
  their pure helpers unit-tested when ``pwsh`` is available (``PWSH`` env var
  or PATH); otherwise those tests skip.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase, TransactionTestCase

from tests.factories import make_admin, make_missionary

REPO = Path(__file__).resolve().parent.parent
WINDOWS_DIR = REPO / "deploy" / "windows"
_REAL_CHMOD = os.chmod


def _load_serve():
    spec = importlib.util.spec_from_file_location(
        "mtc_windows_serve", WINDOWS_DIR / "serve.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _pwsh() -> str | None:
    candidate = os.environ.get("PWSH") or shutil.which("pwsh")
    if candidate and Path(candidate).exists():
        return candidate
    return None


class _WindowsMode:
    """Patch the backup module into its Windows branches on this OS.

    Simulates NTFS semantics for POSIX mode bits: ``os.chmod`` cannot make a
    file 0600 there, so it is turned into a no-op and new files keep the
    host's default (looser) mode, exactly what verify sees on Windows.
    """

    def __init__(self):
        self._patches = []

    def __enter__(self):
        from apps.operations import backup as backup_mod

        def _no_chmod(path, mode, *args, **kwargs):
            raise AssertionError("os.chmod must not be used on Windows")

        self._patches = [
            mock.patch.object(backup_mod, "IS_WINDOWS", True),
            mock.patch.object(backup_mod.os, "chmod", _no_chmod),
        ]
        for patch in self._patches:
            patch.start()
        return backup_mod

    def __exit__(self, *exc):
        for patch in reversed(self._patches):
            patch.stop()
        return False


class WindowsBackupBranchTests(TransactionTestCase):
    """Real backups/restores through the Windows code paths."""

    def test_backup_verify_prune_restore_cycle_in_windows_mode(self):
        from apps.operations import guards
        from apps.roster.models import Missionary

        admin, _ = make_admin()
        person = make_missionary(admin)
        with tempfile.TemporaryDirectory() as tmp, _WindowsMode() as backup_mod:
            dest = Path(tmp) / "nested" / "pre-update-20260101-000000.sqlite3"
            info = backup_mod.backup_sqlite(
                source_path=backup_mod.live_db_path(), dest_path=dest)
            self.assertTrue(info["completed"])
            # The file is NOT 0600 (Windows has no such mode); verification
            # must still pass because the install-root ACL is the control.
            _REAL_CHMOD(dest, 0o644)
            backup_mod.verify_sqlite_backup(dest)
            self.assertEqual(backup_mod.read_manifest(dest)["sha256"],
                             backup_mod.backup_digest(dest))
            self.assertEqual(backup_mod.protected_recovery_backup(tmp + "/nested"), dest)
            result = backup_mod.prune_backups(backup_dir=dest.parent, retention_days=30)
            self.assertEqual(result["protected"], str(dest))
            self.assertFalse([p for p in os.listdir(dest.parent) if ".tmp-" in p])

            make_missionary(admin)  # newer work the backup lacks
            live_dir = backup_mod.live_db_path().parent
            staged_before = {p for p in os.listdir(live_dir) if ".tmp-" in p}
            token = guards.enter_maintenance(owner="test", reason="windows restore")
            try:
                restored = backup_mod.restore_sqlite(
                    backup_path=dest, dest_path=backup_mod.live_db_path())
                self.assertIn("preserved", restored)
            finally:
                guards.exit_maintenance(token)
            self.assertEqual(list(Missionary.objects.values_list("pk", flat=True)),
                             [person.pk])
            # No staging files (or their WAL/SHM sidecars) are left behind.
            staged_after = {p for p in os.listdir(live_dir) if ".tmp-" in p}
            self.assertEqual(staged_after - staged_before, set())

    def test_prune_removes_verify_sidecars_with_the_backup(self):
        import time as _time

        from apps.operations import backup as backup_mod

        with tempfile.TemporaryDirectory() as tmp:
            old = Path(tmp) / "daily-20200101-000000.sqlite3"
            backup_mod.backup_sqlite(source_path=backup_mod.live_db_path(), dest_path=old)
            backup_mod.verify_sqlite_backup(old)  # read-only open of a WAL-flagged file
            for suffix in ("-wal", "-shm"):
                Path(str(old) + suffix).touch()  # as left behind on any OS
            ancient = _time.time() - 40 * 86400
            os.utime(old, (ancient, ancient))
            result = backup_mod.prune_backups(backup_dir=tmp, retention_days=30)
            self.assertEqual(result["deleted"], [str(old)])
            self.assertEqual(os.listdir(tmp), [])

    def test_posix_still_refuses_loose_permissions(self):
        from apps.operations import backup as backup_mod

        self.assertEqual(backup_mod.IS_WINDOWS, os.name == "nt")
        if backup_mod.IS_WINDOWS:
            self.skipTest("POSIX permission contract")
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "b.sqlite3"
            backup_mod.backup_sqlite(source_path=backup_mod.live_db_path(), dest_path=dest)
            self.assertEqual(oct(dest.stat().st_mode & 0o777), "0o600")
            os.chmod(dest, 0o644)
            with self.assertRaises(ValueError):
                backup_mod.verify_sqlite_backup(dest)

    def test_restore_swaps_with_django_connections_closed(self):
        """Windows cannot replace a file another handle holds open."""
        from django.db import connection

        from apps.operations import backup as backup_mod
        from apps.operations import guards

        make_admin()
        seen = []
        real_replace = backup_mod._replace

        def _spy(src, dst):
            if Path(dst) == backup_mod.live_db_path():
                seen.append(connection.connection is None)
            return real_replace(src, dst)

        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "r.sqlite3"
            backup_mod.backup_sqlite(source_path=backup_mod.live_db_path(), dest_path=src)
            token = guards.enter_maintenance(owner="test", reason="swap")
            try:
                with mock.patch.object(backup_mod, "_replace", _spy):
                    backup_mod.restore_sqlite(backup_path=src,
                                              dest_path=backup_mod.live_db_path())
            finally:
                guards.exit_maintenance(token)
        self.assertEqual(seen, [True])


class WindowsBackupHelperTests(SimpleTestCase):
    def test_restrict_is_a_noop_on_windows(self):
        with tempfile.TemporaryDirectory() as tmp, _WindowsMode() as backup_mod:
            target = Path(tmp) / "f"
            target.write_bytes(b"x")
            backup_mod._restrict(target)  # would raise via the chmod guard
            backup_mod._restrict(Path(tmp))

    def test_fsync_file_uses_a_writable_handle_on_windows(self):
        from apps.operations import backup as backup_mod

        calls = []
        real_open = os.open

        def _spy(path, flags, *args, **kwargs):
            calls.append(flags)
            return real_open(path, flags, *args, **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "f"
            target.write_bytes(b"x")
            with mock.patch.object(backup_mod.os, "open", _spy):
                with mock.patch.object(backup_mod, "IS_WINDOWS", True):
                    backup_mod._fsync_file(target)
                with mock.patch.object(backup_mod, "IS_WINDOWS", False):
                    backup_mod._fsync_file(target)
        self.assertTrue(calls[0] & os.O_RDWR, "Windows FlushFileBuffers needs write access")
        self.assertEqual(calls[1] & (os.O_RDWR | os.O_WRONLY), 0, "POSIX stays read-only")

    def test_fsync_dir_skips_on_windows_where_o_directory_is_missing(self):
        from apps.operations import backup as backup_mod

        saved = getattr(os, "O_DIRECTORY", None)
        if saved is not None:
            del os.O_DIRECTORY  # the Windows os module has no O_DIRECTORY
        try:
            with tempfile.TemporaryDirectory() as tmp:
                with mock.patch.object(backup_mod, "IS_WINDOWS", True):
                    backup_mod._fsync_dir(Path(tmp))  # no AttributeError
        finally:
            if saved is not None:
                os.O_DIRECTORY = saved

    def test_replace_retries_transient_sharing_violations_on_windows(self):
        from apps.operations import backup as backup_mod

        attempts = []

        def _flaky(src, dst):
            attempts.append(1)
            if len(attempts) < 3:
                raise PermissionError(13, "file in use by another process")

        with mock.patch.object(backup_mod.os, "replace", _flaky), \
                mock.patch("time.sleep") as sleep:
            with mock.patch.object(backup_mod, "IS_WINDOWS", True):
                backup_mod._replace("a", "b")
            self.assertEqual(len(attempts), 3)
            self.assertEqual(sleep.call_count, 2)
            attempts.clear()
            with mock.patch.object(backup_mod, "IS_WINDOWS", False):
                with self.assertRaises(PermissionError):
                    backup_mod._replace("a", "b")
            self.assertEqual(len(attempts), 1, "POSIX never retries")

    def test_replace_gives_up_after_bounded_attempts(self):
        from apps.operations import backup as backup_mod

        def _locked(src, dst):
            raise PermissionError(13, "still locked")

        with mock.patch.object(backup_mod.os, "replace", _locked), \
                mock.patch("time.sleep"), \
                mock.patch.object(backup_mod, "IS_WINDOWS", True):
            with self.assertRaises(PermissionError):
                backup_mod._replace("a", "b")


class ServeLauncherTests(SimpleTestCase):
    def setUp(self):
        self.serve = _load_serve()

    def test_parse_env_text_rules(self):
        text = ("\ufeff# comment\r\n\r\n"
                "DJANGO_DEBUG=0\r\n"
                "  DJANGO_DB_PATH = C:\\MTCSupplies\\data\\db.sqlite3  \n"
                "QUOTED=\"a b # c\"\n"
                "SINGLE='x=y'\n"
                "EMPTY=\n"
                "\t# indented comment\n"
                "DJANGO_DEBUG=1\r"
                "LAST=done")
        self.assertEqual(self.serve.parse_env_text(text), {
            "DJANGO_DEBUG": "1",
            "DJANGO_DB_PATH": "C:\\MTCSupplies\\data\\db.sqlite3",
            "QUOTED": "a b # c",
            "SINGLE": "x=y",
            "EMPTY": "",
            "LAST": "done",
        })

    def test_parse_env_text_rejects_malformed_lines_without_echoing_values(self):
        for bad in ("JUST-TEXT", "1KEY=v", "BAD KEY=v", "=nokey"):
            with self.assertRaises(ValueError) as ctx:
                self.serve.parse_env_text(f"OK=1\n{bad}\n")
            self.assertIn("line 2", str(ctx.exception))
            self.assertNotIn(bad, str(ctx.exception))
        with self.assertRaises(ValueError) as ctx:
            self.serve.parse_env_text("DJANGO_SECRET_KEY supersecretvalue\n")
        self.assertNotIn("supersecretvalue", str(ctx.exception))

    def test_load_env_file_never_overrides_set_variables(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "settings.env"
            env_file.write_text("A=from-file\nB=from-file\n", encoding="utf-8")
            environ = {"A": "already-set"}
            applied = self.serve.load_env_file(env_file, environ)
        self.assertEqual(environ, {"A": "already-set", "B": "from-file"})
        self.assertEqual(applied, ["B"])

    def test_server_options_trust_only_caddy_proto_on_loopback(self):
        options = self.serve.server_options()
        self.assertEqual(options["listen"], "127.0.0.1:8000")
        self.assertEqual(options["threads"], 8)
        self.assertEqual(options["trusted_proxy"], "127.0.0.1")
        self.assertEqual(options["trusted_proxy_headers"], {"x-forwarded-proto"})
        self.assertTrue(options["clear_untrusted_proxy_headers"])

    def test_main_serves_django_with_waitress_stub(self):
        calls = []
        stub = types.ModuleType("waitress")
        stub.serve = lambda app, **kw: calls.append((app, kw))
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "settings.env"
            env_file.write_text("MTC_WINDOWS_TEST_ONLY=1\n", encoding="utf-8")
            log_file = Path(tmp) / "logs" / "app.log"
            out, err = sys.stdout, sys.stderr
            try:
                with mock.patch.dict(sys.modules, {"waitress": stub}), \
                        mock.patch.dict(os.environ, {}), \
                        mock.patch.object(self.serve, "port_in_use", return_value=False), \
                        mock.patch.object(self.serve, "_timestamp_logs"):
                    code = self.serve.main(["--env-file", str(env_file),
                                            "--log-file", str(log_file)])
                    self.assertEqual(os.environ.get("MTC_WINDOWS_TEST_ONLY"), "1")
            finally:
                if sys.stdout is not out:
                    sys.stdout.close()
                sys.stdout, sys.stderr = out, err
            self.assertEqual(code, 0)
            from django.core.handlers.wsgi import WSGIHandler

            self.assertIsInstance(calls[0][0], WSGIHandler)
            self.assertEqual(calls[0][1]["listen"], "127.0.0.1:8000")
            self.assertIn("MTC Supplies", log_file.read_text(encoding="utf-8"))
            self.assertNotIn("MTC_WINDOWS_TEST_ONLY=1", log_file.read_text(encoding="utf-8"))

    def test_main_refuses_a_second_server_and_missing_settings(self):
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "settings.env"
            env_file.write_text("X=1\n", encoding="utf-8")
            with mock.patch.object(self.serve, "port_in_use", return_value=True), \
                    mock.patch.dict(os.environ, {}), \
                    mock.patch.object(sys, "stderr", stderr):
                self.assertEqual(self.serve.main(["--env-file", str(env_file)]), 3)
                self.assertEqual(self.serve.main(["--env-file", str(Path(tmp) / "nope")]), 2)
                with mock.patch.dict(os.environ, {self.serve.ENV_FILE_VAR: ""}):
                    self.assertEqual(self.serve.main([]), 2)

    def test_port_probe_detects_a_listener(self):
        import socket

        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            sock.listen(1)
            port = sock.getsockname()[1]
            self.assertTrue(self.serve.port_in_use("127.0.0.1", port))
        self.assertFalse(self.serve.port_in_use("127.0.0.1", port))

    def test_log_rotation_keeps_one_previous_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "app.log"
            log.write_bytes(b"x" * 20)
            self.serve.rotate_log(log, max_bytes=10)
            self.assertFalse(log.exists())
            self.assertEqual((Path(tmp) / "app.log.1").read_bytes(), b"x" * 20)
            log.write_bytes(b"y")
            self.serve.rotate_log(log, max_bytes=10)
            self.assertTrue(log.exists())


class NativeReleaseContractTests(SimpleTestCase):
    def test_version_file_backs_app_version_when_env_is_empty(self):
        version = (REPO / "VERSION").read_text(encoding="utf-8").strip()
        self.assertRegex(version, r"^[0-9]+\.[0-9]+\.[0-9]+$")
        code = ("import django, os;"
                "os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings');"
                "django.setup();"
                "from django.conf import settings; print(settings.APP_VERSION)")
        base = {k: v for k, v in os.environ.items() if k != "APP_VERSION"}
        for extra, expected in (({}, version), ({"APP_VERSION": ""}, version),
                                ({"APP_VERSION": "9.9.9"}, "9.9.9")):
            proc = subprocess.run([sys.executable, "-c", code], cwd=REPO,
                                  env={**base, **extra}, capture_output=True,
                                  text=True, timeout=120)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(proc.stdout.strip(), expected)

    def test_waitress_is_pinned_for_the_native_server(self):
        lines = (REPO / "requirements.prod.txt").read_text(encoding="utf-8").splitlines()
        self.assertIn("waitress==3.0.2", lines)


class _ReadyHandler:
    """Tiny /readyz/ stand-in recording the forwarded-proto header."""

    @staticmethod
    def make(state):
        from http.server import BaseHTTPRequestHandler

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - http.server API
                state["proto"] = self.headers.get("X-Forwarded-Proto")
                body = json.dumps(state["body"]).encode()
                self.send_response(state["status"])
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        return Handler


class KubernetesExampleTests(SimpleTestCase):
    def test_kubernetes_example_keeps_one_sqlite_writer(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
        docs = []
        for path in sorted((REPO / "deploy" / "kubernetes").glob("*.yaml")):
            docs.extend(d for d in yaml.safe_load_all(path.read_text(encoding="utf-8")) if d)
        kinds = {d["kind"] for d in docs}
        self.assertTrue({"Namespace", "Secret", "ConfigMap", "PersistentVolumeClaim",
                         "Deployment", "Service", "Ingress"} <= kinds, kinds)
        deployment = next(d for d in docs if d["kind"] == "Deployment")
        spec = deployment["spec"]
        self.assertEqual(spec["replicas"], 1)
        self.assertEqual(spec["strategy"]["type"], "Recreate")
        pod = spec["template"]["spec"]
        self.assertEqual(pod["securityContext"]["runAsUser"], 10001)
        self.assertEqual(pod["securityContext"]["fsGroup"], 10001)
        container = pod["containers"][0]
        self.assertNotIn(":latest", container["image"])
        self.assertEqual(container["image"].rsplit(":", 1)[1],
                         next(e["value"] for e in container["env"] if e["name"] == "APP_VERSION"))
        for probe, path in (("readinessProbe", "/readyz/"), ("livenessProbe", "/healthz/")):
            get = container[probe]["httpGet"]
            self.assertEqual(get["path"], path)
            self.assertIn({"name": "X-Forwarded-Proto", "value": "https"}, get["httpHeaders"])
            self.assertIn({"name": "Host", "value": "localhost"}, get["httpHeaders"])
        pvc = next(d for d in docs if d["kind"] == "PersistentVolumeClaim")
        self.assertEqual(pvc["spec"]["accessModes"], ["ReadWriteOnce"])
        config = next(d for d in docs if d["kind"] == "ConfigMap")["data"]
        self.assertTrue(config["DJANGO_DB_PATH"].startswith("/app/data/"))
        self.assertEqual(config["DJANGO_DEBUG"], "0")
        ingress = next(d for d in docs if d["kind"] == "Ingress")
        host = ingress["spec"]["rules"][0]["host"]
        self.assertIn(host, config["DJANGO_ALLOWED_HOSTS"].split(","))
        self.assertIn(f"https://{host}", config["DJANGO_CSRF_TRUSTED_ORIGINS"].split(","))
        # The example Secret must never be applied by the kustomization, and
        # its placeholder is the one the app refuses in production.
        kustomization = next(d for d in docs if d["kind"] == "Kustomization")
        self.assertNotIn("secret.example.yaml", kustomization["resources"])
        secret = next(d for d in docs if d["kind"] == "Secret")
        self.assertEqual(secret["stringData"]["DJANGO_SECRET_KEY"], "dev-insecure-secret-key-change-me")


class CaddyConfigTests(SimpleTestCase):
    """Both Caddyfiles serve the public root certificate at /ca.crt."""

    def test_docker_caddyfile_serves_only_the_public_root_certificate(self):
        docker = (REPO / "deploy" / "Caddyfile").read_text(encoding="utf-8")
        self.assertIn("handle /ca.crt", docker)
        self.assertIn("root * /data/caddy/pki/authorities/local", docker)
        self.assertIn("rewrite * /root.crt", docker)
        self.assertNotIn("root.key", docker)
        # The site stays parameterized on the Compose-provided address/port.
        self.assertIn("https://{$APP_HOST}:{$APP_PORT}", docker)

    def test_caddyfiles_adapt_with_the_pinned_caddy(self):
        caddy = os.environ.get("CADDY") or shutil.which("caddy")
        if not caddy:
            self.skipTest("caddy not available (set CADDY=/path/to/caddy 2.10.2)")
        env = {**os.environ, "APP_HOST": "192.168.1.10", "APP_PORT": "8443"}
        with tempfile.TemporaryDirectory() as tmp:
            docker = subprocess.run(
                [caddy, "adapt", "--config", str(REPO / "deploy" / "Caddyfile"),
                 "--adapter", "caddyfile"], capture_output=True, text=True,
                timeout=60, env=env, cwd=tmp)
            self.assertEqual(docker.returncode, 0, docker.stderr)
            self.assertIn("/root.crt", docker.stdout)
            pwsh = _pwsh()
            if pwsh:
                generated = subprocess.run(
                    [pwsh, "-NoProfile", "-NonInteractive", "-File",
                     str(REPO / "tests" / "windows_ps_checks.ps1"), "-Mode", "caddyfile",
                     "-Root", "C:\\MTCSupplies", "-HostAddress", "192.168.1.10", "-Port", "8443"],
                    capture_output=True, text=True, timeout=120, cwd=REPO)
                self.assertEqual(generated.returncode, 0, generated.stderr)
                path = Path(tmp) / "Caddyfile"
                path.write_text(generated.stdout, encoding="utf-8")
                windows = subprocess.run(
                    [caddy, "adapt", "--config", str(path), "--adapter", "caddyfile"],
                    capture_output=True, text=True, timeout=60, cwd=tmp)
                self.assertEqual(windows.returncode, 0, windows.stderr)
                adapted = json.loads(windows.stdout)
                self.assertEqual(adapted["apps"]["http"]["servers"]["srv0"]["listen"], [":8443"])
                self.assertIn("C:/MTCSupplies/data/caddy/caddy/pki/authorities/local", windows.stdout)


class PowerShellScriptTests(SimpleTestCase):
    """Parse + PS 5.1 compatibility scan + pure-helper unit tests (pwsh)."""

    def setUp(self):
        self.pwsh = _pwsh()
        if not self.pwsh:
            self.skipTest("pwsh not available (set PWSH=/path/to/pwsh)")

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [self.pwsh, "-NoProfile", "-NonInteractive", "-File",
             str(REPO / "tests" / "windows_ps_checks.ps1"), *args],
            capture_output=True, text=True, timeout=300, cwd=REPO)

    def test_scripts_parse_and_avoid_powershell7_only_syntax(self):
        proc = self._run("-Mode", "lint")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("lint ok", proc.stdout)

    def test_pure_powershell_helpers(self):
        proc = self._run("-Mode", "unit")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("unit ok", proc.stdout)

    def test_settings_parsers_agree_between_python_and_powershell(self):
        sample = ("\ufeff# c\r\nA=1\r\n  B = 'two words'  \nC=\"x=y\"\nD=\n"
                  "E=C:\\MTCSupplies\\data\\db.sqlite3\nA=last\r\tF\t=\tg\t\n")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.env"
            path.write_bytes(sample.encode("utf-8"))
            proc = self._run("-Mode", "parse", "-Path", str(path))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        ps_values = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertEqual(ps_values, _load_serve().parse_env_text(sample))

    def test_readiness_probe_sends_https_proto_and_reads_status(self):
        import threading
        from http.server import ThreadingHTTPServer

        state = {"status": 200, "proto": None,
                 "body": {"ok": True, "version": "1.2.3", "maintenance": True}}
        server = ThreadingHTTPServer(("127.0.0.1", 0), _ReadyHandler.make(state))
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            ready = json.loads(self._run("-Mode", "readiness", "-Port", str(port)).stdout)
            self.assertTrue(ready["Ok"])
            self.assertEqual(ready["Version"], "1.2.3")
            self.assertTrue(ready["Maintenance"])
            self.assertTrue(ready["PortOpen"])
            self.assertEqual(state["proto"], "https")
            state.update(status=503, body={"ok": False, "reason": "migrations-pending"})
            pending = json.loads(self._run("-Mode", "readiness", "-Port", str(port)).stdout)
            self.assertFalse(pending["Ok"])
            self.assertTrue(pending["Reachable"])
            self.assertEqual(pending["StatusCode"], 503)
            self.assertEqual(pending["Reason"], "migrations-pending")
        finally:
            server.shutdown()
            server.server_close()
        closed = json.loads(self._run("-Mode", "readiness", "-Port", str(port)).stdout)
        self.assertFalse(closed["Reachable"])
        self.assertFalse(closed["PortOpen"])
