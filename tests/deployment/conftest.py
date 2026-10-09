"""Isolated Compose fixtures: real containers, throwaway volumes.

Every test brings up the actual `compose.test.yaml` stack (Gunicorn +
Caddy with a local TLS certificate) under a unique project name, migrates a
synthetic database, and tears everything down afterwards — volumes included.
No production data, no secrets, no host ports beyond 127.0.0.1:18443.
"""

from __future__ import annotations

import json
import ssl
import subprocess
import urllib.request
import uuid

import pytest

COMPOSE = ["docker", "compose", "-f", "compose.test.yaml"]
BASE_URL = "https://127.0.0.1:18443"
_CTX = ssl._create_unverified_context()


def run(*args, timeout=300, check=True, cwd=".", capture=True):
    proc = subprocess.run(
        list(args), cwd=cwd, timeout=timeout,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
        text=True)
    if check and proc.returncode != 0:
        raise AssertionError(f"{' '.join(args)} failed:\n{proc.stdout}")
    return proc


@pytest.fixture(scope="session")
def test_image():
    run("docker", "build", "-t", "supplies-tracker:test", ".", timeout=900)
    return "supplies-tracker:test"


@pytest.fixture()
def stack(test_image):
    name = f"suppliestest{uuid.uuid4().hex[:10]}"
    prefix = [*COMPOSE, "-p", name]
    run(*prefix, "up", "-d", timeout=600)
    try:
        run(*prefix, "exec", "-T", "app",
            "python", "manage.py", "migrate", "--noinput", timeout=300)
        _wait_ready()
        yield {"project": name, "prefix": prefix}
    finally:
        run(*prefix, "down", "-v", timeout=300, check=False)


def compose_exec(stack, *cmd, timeout=300):
    proc = run(*stack["prefix"], "exec", "-T", "app", *cmd,
               timeout=timeout, check=False)
    return proc


def https_get(path: str, timeout=20):
    req = urllib.request.Request(BASE_URL + path)
    with urllib.request.urlopen(req, context=_CTX, timeout=timeout) as resp:
        return resp.status, resp.read()


def wait_until(predicate, timeout=180, interval=3, label="condition"):
    import time

    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            if predicate():
                return True
        except Exception as exc:  # noqa: BLE001 - keep polling through errors
            last = exc
        time.sleep(interval)
    raise AssertionError(f"timed out waiting for {label}: {last!r}")


def _wait_ready():
    def _ready():
        status, body = https_get("/readyz/")
        payload = json.loads(body.decode())
        return status == 200 and payload.get("ok") is True

    wait_until(_ready, label="compose test stack readiness")
