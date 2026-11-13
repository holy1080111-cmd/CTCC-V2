from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.config.settings import get_settings
from app.database.native_pg_tls_v1 import configured_native_pg_tls_context_v1

settings = get_settings()
native_pg_tls_context = configured_native_pg_tls_context_v1(
    ca_file=settings.ctcc_native_pg_tls_ca_file,
    ca_sha256=settings.ctcc_native_pg_tls_ca_sha256,
    database_url=settings.database_url,
)

engine_options: dict[str, object] = {"pool_pre_ping": True}
if native_pg_tls_context is not None:
    engine_options["connect_args"] = {"ssl": native_pg_tls_context}
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
