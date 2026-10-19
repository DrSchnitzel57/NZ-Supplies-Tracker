"""Synthetic test suites.

The file-backed test database lives in the gitignored `.local/` folder, which a
fresh checkout (or a CI runner) does not have yet. Create it here: both
`manage.py test` and pytest import this package before the test database is set up.
"""

from pathlib import Path

(Path(__file__).resolve().parent.parent / ".local").mkdir(mode=0o700, exist_ok=True)
