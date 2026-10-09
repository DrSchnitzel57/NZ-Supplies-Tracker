"""Shim so bare `manage.py test` discovers the auth suite."""
from tests.auth import *  # noqa: F401,F403
