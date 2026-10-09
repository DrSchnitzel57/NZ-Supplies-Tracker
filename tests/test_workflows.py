"""Shim so bare `manage.py test` discovers the workflows suite."""
from tests.workflows import *  # noqa: F401,F403
