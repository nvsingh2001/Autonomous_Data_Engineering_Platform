"""Every test runs against fakeredis — the suite must pass with no Redis
listening anywhere (CI has none). Tests that want a fresh store still use
their own fixtures; this just guarantees no code path reaches a real socket."""

import fakeredis
import pytest

from app import run_store


@pytest.fixture(autouse=True)
def _hermetic_redis():
    run_store.use_client(fakeredis.FakeRedis(decode_responses=True))
    yield
    run_store.use_client(None)
