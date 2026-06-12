"""Test-suite bootstrap: keep every test hermetic.

``apps.api.services.db`` creates its SQLAlchemy engine at import time using
``SLM_FORGE_DB_URL`` (default: the Docker path ``/app/data/slm_forge.db``,
which doesn't exist on dev machines or CI). Point it at a throwaway SQLite
file before any test imports the API package.
"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault(
    "SLM_FORGE_DB_URL",
    f"sqlite:///{os.path.join(tempfile.gettempdir(), 'slm_forge_test_base.db')}",
)
