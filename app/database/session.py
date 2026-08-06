from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config.settings import get_settings

settings = get_settings()

engine_options: dict[str, object] = {"pool_pre_ping": True}
if settings.environment == "test":
    # TestClient and pytest-asyncio may use different event loops. Never reuse
    # asyncpg connections across those loops.
    engine_options["poolclass"] = NullPool
else:
    engine_options["pool_recycle"] = 1800

engine: AsyncEngine = create_async_engine(settings.database_url, **engine_options)

AsyncSessionFactory = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db_session() -> AsyncIterator[AsyncSession]:
    async with AsyncSessionFactory() as session:
        yield session
