"""SQLite transient-lock retry helper.

Production uses a file database with WAL + bounded busy timeout, so a second
writer normally waits. Under contention (and on shared-cache test databases)
SQLite can still raise transient OperationalErrors such as "database is
locked" or "database table is locked". Retrying the whole atomic block lets
the loser re-read fresh state and raise the proper domain error instead of a
raw database error.
"""

from __future__ import annotations

import time

from django.db import OperationalError, transaction

_TRANSIENT_MARKERS = (
    "database is locked",
    "database table is locked",
    "database is busy",
    "database schema is locked",
)


def is_transient_lock(exc: BaseException) -> bool:
    if not isinstance(exc, OperationalError):
        return False
    message = str(exc).lower()
    return any(marker in message for marker in _TRANSIENT_MARKERS)


def transact(fn, attempts: int = 12, base_delay: float = 0.03):
    """Run fn() inside an atomic block, retrying transient SQLite locks."""
    last: BaseException | None = None
    for attempt in range(attempts):
        try:
            with transaction.atomic():
                return fn()
        except OperationalError as exc:
            if not is_transient_lock(exc) or attempt == attempts - 1:
                raise
            last = exc
            time.sleep(base_delay * (attempt + 1))
    raise last  # pragma: no cover
