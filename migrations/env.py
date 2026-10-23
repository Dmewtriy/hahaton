from alembic import context

from korsik import models  # noqa: F401
from korsik.config import Settings
from korsik.db import Base, Database, UTCDateTime

config = context.config
settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
target_metadata = Base.metadata


def render_item(kind, item, autogen_context):
    if kind == "type" and isinstance(item, UTCDateTime):
        return "sa.DateTime(timezone=True)"
    return False


if context.is_offline_mode():
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    database = Database(settings.database_url)
    with database.engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_as_batch=connection.dialect.name == "sqlite",
            render_item=render_item,
        )
        with context.begin_transaction():
            context.run_migrations()
    database.engine.dispose()
