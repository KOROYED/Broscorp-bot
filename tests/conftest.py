import os

# Settings are read at import time, so fake values must be set before any bot import.
# Real environment variables take priority over .env, so a local .env doesn't leak in.
os.environ.update(
    BOT_TOKEN="123456:test-token",
    GROQ_API_KEY="test-key",
    GROQ_MODEL="test-model",
    GROQ_ROUTER_MODEL="test-router-model",
)

import pytest  # noqa: E402

from bot.db import create_tables, make_engine, make_session_factory  # noqa: E402


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def session_factory(tmp_path):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    await create_tables(engine)
    yield make_session_factory(engine)
    await engine.dispose()


@pytest.fixture
async def session(session_factory):
    async with session_factory() as session:
        yield session
