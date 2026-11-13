import asyncio
import os
import sys
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy import text

# Set environment before loading application
os.environ["ENVIRONMENT"] = "testing"
os.environ["DEBUG"] = "False"
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["ASYNC_DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"

# Add backend directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database.session import Base, get_async_db
from app.main import app
from app.workers.celery_app import celery_app

# Configure Celery for testing: execute tasks eagerly without requiring live Redis
celery_app.conf.update(
    task_always_eager=True,
    task_eager_propagates=True,
    broker_url="memory://",
    result_backend="rpc://",
)

# Use in-memory SQLite with StaticPool so all connections share the same in-memory database
TEST_ASYNC_DB_URL = "sqlite+aiosqlite:///:memory:"
test_async_engine = create_async_engine(
    TEST_ASYNC_DB_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
    echo=False,
)
TestingAsyncSessionLocal = async_sessionmaker(
    bind=test_async_engine,
    class_=AsyncSession,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="function", autouse=True)
async def setup_test_database():
    # Re-create clean schema before each test
    async with test_async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


async def override_get_async_db():
    async with TestingAsyncSessionLocal() as session:
        yield session


app.dependency_overrides[get_async_db] = override_get_async_db


@pytest_asyncio.fixture(scope="function")
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


@pytest_asyncio.fixture(scope="function")
async def db_session():
    async with TestingAsyncSessionLocal() as session:
        yield session
