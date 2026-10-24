from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from korsik.db import Base


def test_upgrade_populated_legacy_database_preserves_links_answers_and_scope(tmp_path, monkeypatch):
    from conftest import employee, event, scenario
    from sqlalchemy import select

    from korsik.config import Settings
    from korsik.models import (
        Branch,
        Department,
        Employee,
        Identity,
        Scenario,
        State,
        SurveyAnswer,
        SurveyRun,
        SurveyTemplate,
    )
    from korsik.runtime import Runtime
    from korsik.worker import Worker

    url = "sqlite:///" + str(tmp_path / "populated.sqlite")
    monkeypatch.setenv("KORSIK_DATABASE_URL", url)
    monkeypatch.setenv("KORSIK_BOOTSTRAP_TOKEN", "migration-test-token-000000000000000000000")
    monkeypatch.setenv("KORSIK_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KORSIK_ENVIRONMENT", "test")
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.upgrade(config, "head")
    runtime = Runtime(Settings(transport="mock", ml_mode="stub", max_ca_file=None))
    runtime.bootstrap()
    with runtime.db.transaction() as session:
        session.add(Branch(code="A", name="A", timezone="Europe/Moscow"))
        session.add_all(
            [
                Identity(id="hr-a", name="HR", roles=["hr"], branch_codes=["A"]),
                Identity(id="manager", name="Manager", roles=["manager"], branch_codes=["A"]),
            ]
        )
        session.add(State(key="clock", value={"now": "2026-10-23T06:00:00+00:00"}))
    employee(runtime)
    scenario_id = scenario(runtime)
    with runtime.db.transaction() as session:
        session.get(Scenario, scenario_id).branch_codes = ["A"]
    Worker(runtime).tick()
    event(runtime, "8")
    monkeypatch.setenv("KORSIK_DATABASE_URL", runtime.settings.database_url)
    monkeypatch.setenv("KORSIK_BOOTSTRAP_TOKEN", runtime.settings.bootstrap_token)
    monkeypatch.setenv("KORSIK_DATA_DIR", str(runtime.settings.data_dir))
    monkeypatch.setenv("KORSIK_ENVIRONMENT", "test")
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    runtime.db.engine.dispose()
    command.downgrade(config, "0001")
    command.upgrade(config, "head")
    command.check(config)
    with runtime.db.transaction() as session:
        emp = session.get(Employee, "e1")
        assert session.get(Department, emp.department_id).name == "Operations"
        assert session.scalar(select(SurveyRun)).status == "completed"
        assert session.scalar(select(SurveyAnswer)).value["answer"] == 8
        assert session.scalar(select(SurveyTemplate)).branch_codes == ["A"]
    runtime.close()


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
