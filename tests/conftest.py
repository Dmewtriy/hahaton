from datetime import date

import pytest
from fastapi.testclient import TestClient

from korsik.access import token_hash
from korsik.api import create_app
from korsik.calendar import add_months
from korsik.config import Settings
from korsik.db import Base
from korsik.models import Branch, Employee, Identity, Scenario, State
from korsik.runtime import Runtime
from korsik.scenarios import rebuild_schedule
from korsik.schemas import ScenarioDefinition
from korsik.worker import Worker, ingest

BOOTSTRAP = "test-bootstrap-token-00000000000000000000"


@pytest.fixture
def runtime(tmp_path):
    settings = Settings(
        environment="test",
        bootstrap_token=BOOTSTRAP,
        database_url="sqlite:///" + str(tmp_path / "db.sqlite"),
        data_dir=tmp_path / "data",
        enable_demo=True,
        max_webhook_secret="webhook-secret-test",
    )
    runtime = Runtime(settings)
    Base.metadata.create_all(runtime.db.engine)
    runtime.bootstrap()
    with runtime.db.transaction() as session:
        session.add(State(key="clock", value={"now": "2026-10-23T06:00:00+00:00"}))
        session.add(State(key="digest_policy", value={"enabled": False}))
        for code in ["A", "B"]:
            session.add(Branch(code=code, name=code, timezone="Europe/Moscow"))
        session.add_all(
            [
                Identity(
                    id="hr-a",
                    name="HR A",
                    roles=["hr"],
                    branch_codes=["A"],
                    token_hash=token_hash("hr-a-key"),
                    max_user_id="hr-a-max",
                ),
                Identity(
                    id="hr-b",
                    name="HR B",
                    roles=["hr"],
                    branch_codes=["B"],
                    token_hash=token_hash("hr-b-key"),
                    max_user_id="hr-b-max",
                ),
                Identity(
                    id="manager",
                    name="Manager",
                    roles=["manager"],
                    branch_codes=["A"],
                    token_hash=token_hash("manager-key"),
                    max_user_id="manager-max",
                ),
                Identity(
                    id="head",
                    name="Head",
                    roles=["hr_head"],
                    branch_codes=["A"],
                    token_hash=token_hash("head-key"),
                    max_user_id="head-max",
                ),
            ]
        )
    yield runtime
    runtime.close()


@pytest.fixture
def client(runtime):
    with TestClient(create_app(runtime)) as client:
        yield client


def auth(token):
    return {"Authorization": "Bearer " + token}


def clock(runtime, value):
    with runtime.db.transaction() as session:
        session.merge(State(key="clock", value={"now": value}))


def employee(runtime, *, emp_id="e1", branch="A", bound=True, hire=date(2026, 10, 23)):
    with runtime.db.transaction() as session:
        person = Identity(
            id="i-" + emp_id,
            name=emp_id,
            roles=["employee"],
            branch_codes=[branch],
            max_user_id="max-" + emp_id if bound else None,
            token_hash=token_hash("key-" + emp_id),
        )
        session.add(person)
        session.flush()
        emp = Employee(
            id=emp_id,
            identity_id=person.id,
            name=emp_id,
            branch_code=branch,
            department="Operations",
            category="office",
            hire_date=hire,
            probation_end=add_months(hire, 3),
            recruiter_id="hr-a" if branch == "A" else "hr-b",
            manager_id="manager" if branch == "A" else None,
            timezone="Europe/Moscow",
            status="active" if bound else "invited",
        )
        session.add(emp)
        session.flush()
        return emp


def scenario(runtime, *, steps=None, questions=None, branch="A", name="test", version=1):
    if steps is None:
        steps = [
            {
                "id": "survey",
                "text": "Начнём опрос",
                "kind": "survey",
                "survey_id": "s",
                "schedule": {"at": "09:00"},
            }
        ]
    if questions is None:
        questions = [{"id": "rating", "text": "Оценка поддержки", "kind": "rating", "comment_below": 8}]
    data = ScenarioDefinition.model_validate(
        {
            "name": name,
            "version": version,
            "steps": steps,
            "surveys": [{"id": "s", "title": "Опрос", "questions": questions}],
        }
    )
    with runtime.db.transaction() as session:
        definition = Scenario(name=name, version=version, definition=data.model_dump(mode="json"))
        session.add(definition)
        session.flush()
        session.get(Branch, branch).scenario_id = definition.id
        for emp in session.query(Employee).filter_by(branch_code=branch):
            rebuild_schedule(session, emp)
        return definition.id


def event(runtime, text="", *, emp_id="e1", event_id=None, kind="message", payload=None):
    from uuid import uuid4

    key = event_id or uuid4().hex
    with runtime.db.transaction() as session:
        ingest(
            session,
            {"event_id": key, "max_user_id": "max-" + emp_id, "kind": kind, "text": text, "payload": payload},
        )
    Worker(runtime).tick()
    return key
