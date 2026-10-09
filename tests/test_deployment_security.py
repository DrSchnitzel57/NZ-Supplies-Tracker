"""Shim so bare `manage.py test` discovers the deployment_security suite."""
from tests.deployment_security import *  # noqa: F401,F403
