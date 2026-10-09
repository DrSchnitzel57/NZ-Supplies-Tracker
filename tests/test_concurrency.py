"""Shim so bare `manage.py test` discovers the concurrency suite."""
from tests.concurrency import *  # noqa: F401,F403
