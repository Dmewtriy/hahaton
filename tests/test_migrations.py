from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from korsik.db import Base


def test_initial_migration_roundtrip_matches_models(tmp_path, monkeypatch):
    url = "sqlite:///" + str(tmp_path / "migration.sqlite")
    monkeypatch.setenv("KORSIK_DATABASE_URL", url)
    monkeypatch.setenv("KORSIK_BOOTSTRAP_TOKEN", "migration-test-token-000000000000000000000")
    monkeypatch.setenv("KORSIK_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KORSIK_ENVIRONMENT", "test")
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    command.upgrade(config, "head")
    engine = create_engine(url)
    assert set(Base.metadata.tables) <= set(inspect(engine).get_table_names())
    engine.dispose()
    command.check(config)
    command.downgrade(config, "base")
    engine = create_engine(url)
    assert inspect(engine).get_table_names() == ["alembic_version"]
    engine.dispose()
    command.upgrade(config, "head")
