"""Shim so bare `manage.py test` discovers the review-regression suite."""
from tests.review_regressions import *  # noqa: F401,F403
