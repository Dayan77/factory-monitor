from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from .config import settings

engine = create_async_engine(
    settings.database_url,
    # Sprint 7: configurable (DB_POOL_SIZE / DB_MAX_OVERFLOW). Connections above pool_size are closed when returned
    # and the next one pays a new connection + SCRAM authentication — the MQTT ingestion processes keep a pool
    # sized for their concurrency instead (main.py passes INGEST_DB_POOL_SIZE to them).
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    echo=settings.sql_echo,   # SQL_ECHO=true to log statements (slows the MQTT pipeline a lot)
)

AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
