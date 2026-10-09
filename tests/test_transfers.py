"""Shim so bare `manage.py test` discovers the transfers suite."""
from tests.transfers import *  # noqa: F401,F403
