from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from app.core.config import settings


class Base(DeclarativeBase):
    pass


def attach_sqlite_pragmas(async_engine: AsyncEngine) -> None:
    """Attach connect-time listener that issues WAL + foreign_keys pragmas.

    Must be called on every async engine that talks to SQLite — including the
    in-memory engines used in tests. Without `foreign_keys=ON`, SQLite silently
    ignores ON DELETE CASCADE constraints and the test for lot_alloc cleanup
    on sell-deletion will fail.
    """

    @event.listens_for(async_engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


# On the snapshot test stack (FLOWFOLIO_NULL_POOL=true), disable
# connection pooling entirely. SQLAlchemy's AsyncAdaptedQueuePool holds file
# handles that reference the old SQLite inode even after test_db_reset.sh
# atomically swaps the DB file via `mv -f`. NullPool opens a fresh connection
# per request, eliminating the race at the cost of ~1ms overhead per request
# (acceptable for a 20-test hermetic suite).
_pool_kwargs: dict = (
    {"poolclass": NullPool}
    if settings.null_pool
    else {"pool_pre_ping": True}
)

engine = create_async_engine(
    settings.database_url,
    echo=False,      # NEVER echo=True in production — logs SQL with values
    **_pool_kwargs,
)
attach_sqlite_pragmas(engine)


AsyncSessionLocal = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)
async_session_factory = AsyncSessionLocal


async def get_db() -> AsyncSession:  # type: ignore[return]
    async with AsyncSessionLocal() as session:
        yield session
