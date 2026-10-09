"""Shim so bare `manage.py test` discovers the admin_safety suite."""
from tests.admin_safety import *  # noqa: F401,F403
