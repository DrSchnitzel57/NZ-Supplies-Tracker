"""Shim so bare `manage.py test` discovers the reports suite."""
from tests.reports import *  # noqa: F401,F403
