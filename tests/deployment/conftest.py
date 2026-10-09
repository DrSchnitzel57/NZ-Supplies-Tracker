"""Deployment pytest suite (isolated Compose services + file contracts).

Dependencies: pytest, pytest-django (see requirements.txt). No live Docker
daemon required: Compose/Dockerfile/Caddyfile/scripts are verified as
checked-in contracts, while backup/restore/readyz behavior runs against
synthetic Django/SQLite state. Fixtures use tmp_path and the Django test
database only; never production data or secrets.
"""

import pytest

pytestmark = pytest.mark.django_db
