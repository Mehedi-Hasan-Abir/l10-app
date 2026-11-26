"""API tests run against real Postgres and each starts from a fresh seed."""

import pytest


@pytest.fixture(autouse=True)
def _fresh_seed(seeded_db: None) -> None:
    """Autouse only under tests/api; tests/rules stays free of Postgres."""
