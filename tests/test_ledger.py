"""Shim so bare `manage.py test` discovers the ledger suite."""
from tests.ledger import *  # noqa: F401,F403
