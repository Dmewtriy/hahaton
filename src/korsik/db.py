from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy import DateTime, create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator):
    """Keep UTC semantics on both SQLite and PostgreSQL."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None:
            if value.tzinfo is None:
                raise ValueError("Timezone-aware datetime required")
            return value.astimezone(UTC)
        return None

    def process_result_value(self, value, dialect):
        return value.replace(tzinfo=UTC) if value and value.tzinfo is None else value


class Base(DeclarativeBase):
    pass


def utcnow():
    return datetime.now(UTC)


class Database:
    def __init__(self, url: str):
        options = (
            {"connect_args": {"check_same_thread": False, "timeout": 30}} if url.startswith("sqlite") else {}
        )
        self.engine = create_engine(url, pool_pre_ping=True, **options)
        if url.startswith("sqlite"):

            @event.listens_for(self.engine, "connect")
            def configure_sqlite(connection, _):
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("PRAGMA journal_mode=WAL")

        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    @contextmanager
    def transaction(self, write_lock=False):
        with self.sessions.begin() as session:
            if write_lock and self.engine.dialect.name == "sqlite":
                session.execute(text("BEGIN IMMEDIATE"))
            yield session
