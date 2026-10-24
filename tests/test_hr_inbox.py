import json
from pathlib import Path

import httpx
from alembic import command
from alembic.config import Config
from conftest import auth, employee, event, scenario
from sqlalchemy import select, text
from test_admin_campaigns import draft, publish

from korsik import tickets
from korsik.commands import PUBLIC_COMMANDS
from korsik.max_client import MAXClient
from korsik.ml import StubML
from korsik.models import (
    Employee,
    HRMessage,
    Identity,
    Outbox,
    QuestionLog,
    Scenario,
    ScheduledStep,
    SurveyAnswer,
    SurveyRun,
    Ticket,
)
from korsik.worker import Worker


def test_onboarding_intro_collects_response_for_hr_without_rag(runtime):
    class NoRAG(StubML):
        def answer(self, payload):
            raise AssertionError("An onboarding response must not reach RAG")

    runtime.ml = NoRAG()
    employee(runtime)
    scenario(
        runtime,
        steps=[
            {"id": "introduction", "kind": "message", "text": "Расскажите рекрутеру о себе", "reply_to": "hr"}
        ],
    )
    Worker(runtime).tick()
    event(runtime, "Мой опыт — обслуживание оборудования")
    with runtime.db.transaction() as session:
        assert not list(session.scalars(select(QuestionLog)))
        assert session.scalar(select(HRMessage)).text == "Мой опыт — обслуживание оборудования"
        assert not session.scalar(select(Ticket)).human_mode
        assert session.scalar(select(ScheduledStep)).status == "completed"
        assert "awaiting_hr_message" not in session.get(Employee, "e1").dialogue


def test_hr_messages_and_reply_do_not_block_survey_or_broadcast(client, runtime):
    employee(runtime)
    scenario(runtime)
    Worker(runtime).tick()
    event(runtime, "/hr")
    event(runtime, "Нужна помощь рекрутера")
    with runtime.db.transaction() as session:
        assert not list(session.scalars(select(SurveyAnswer)))
        assert session.scalar(select(SurveyRun)).position == 0
    response = client.post(
        "/api/admin/inbox/e1/messages",
        headers={**auth("hr-a-key"), "Idempotency-Key": "reply"},
        json={"text": "Помогу разобраться"},
    )
    assert response.status_code == 201, response.text
    event(runtime, "8")
    campaign = draft(client)
    assert publish(client, campaign).status_code == 200
    Worker(runtime).tick()
    with runtime.db.transaction() as session:
        assert session.scalar(select(SurveyRun)).status == "completed"
        assert session.scalar(select(Ticket)).status != "closed"
        step = session.scalar(select(ScheduledStep).where(ScheduledStep.campaign_id == campaign["id"]))
        assert step.status == "dispatched"
        assert session.scalar(select(Outbox).where(Outbox.dedupe_key == "step:" + step.id)).status == "sent"


def test_resume_and_new_survey_cancel_one_message_hr_composer(runtime):
    employee(runtime)
    event(runtime, "/hr")
    scenario(runtime)
    Worker(runtime).tick()
    event(runtime, "8")
    with runtime.db.transaction() as session:
        assert not list(session.scalars(select(Ticket)))
        assert session.scalar(select(SurveyRun)).status == "completed"
    scenario(runtime, name="second")
    # A separate campaign is tested elsewhere; reuse a fresh employee for resume semantics.
    employee(runtime, emp_id="e2")
    scenario(runtime, name="third")
    Worker(runtime).tick()
    event(runtime, "/hr", emp_id="e2")
    event(runtime, "/resume", emp_id="e2")
    event(runtime, "8", emp_id="e2")
    with runtime.db.transaction() as session:
        assert not list(session.scalars(select(Ticket)))
        assert session.scalar(select(SurveyRun).where(SurveyRun.employee_id == "e2")).status == "completed"


def test_inbox_assignment_scope_and_read_cursor_do_not_swallow_new_messages(client, runtime):
    employee(runtime)
    employee(runtime, emp_id="other")
    employee(runtime, emp_id="outside", branch="B")
    with runtime.db.transaction() as session:
        session.get(Employee, "other").recruiter_id = "head"
    event(runtime, "/hr Первое")
    event(runtime, "/hr Второе")
    headers = auth("hr-a-key")
    mine = client.get("/api/admin/inbox", headers=headers).json()
    assert [row["employee_id"] for row in mine] == ["e1"]
    assert mine[0]["unread_count"] == 2
    assert len(client.get("/api/admin/inbox?scope=all", headers=headers).json()) == 2
    assert client.get("/api/admin/inbox/outside", headers=headers).status_code == 403
    assert client.get("/api/admin/inbox/e1", headers=auth("manager-key")).status_code == 403
    history = client.get("/api/admin/inbox/e1", headers=headers).json()["messages"]
    event(runtime, "/hr Третье")
    assert (
        client.post(
            "/api/admin/inbox/e1/read", headers=headers, json={"last_message_id": history[-1]["id"]}
        ).status_code
        == 200
    )
    assert client.get("/api/admin/inbox/summary", headers=headers).json()["unread_messages"] == 1
    latest = client.get("/api/admin/inbox/e1", headers=auth("head-key")).json()["messages"][-1]["id"]
    client.post("/api/admin/inbox/e1/read", headers=auth("head-key"), json={"last_message_id": latest})
    assert client.get("/api/admin/inbox/summary", headers=headers).json()["unread_messages"] == 1
    oldest = client.get("/api/admin/inbox/e1?limit=1", headers=headers).json()
    assert oldest["has_more"] and len(oldest["messages"]) == 1


def test_inbox_reply_is_idempotent_and_uses_one_history(client, runtime):
    employee(runtime)
    event(runtime, "/hr Вопрос")
    headers = {**auth("hr-a-key"), "Idempotency-Key": "same-message"}
    one = client.post("/api/admin/inbox/e1/messages", headers=headers, json={"text": "Ответ HR"})
    two = client.post("/api/admin/inbox/e1/messages", headers=headers, json={"text": "Ответ HR"})
    assert one.status_code == two.status_code == 201 and one.json() == two.json()
    assert (
        client.post(
            "/api/admin/inbox/e1/messages", headers=headers, json={"text": "Другой ответ"}
        ).status_code
        == 409
    )
    assert client.post("/api/admin/inbox/e1/resolve", headers=auth("hr-a-key")).status_code == 409
    event(runtime, "/hr Новое сообщение")
    history = client.get("/api/admin/inbox/e1", headers=auth("hr-a-key")).json()["messages"]
    assert [m["text"] for m in history if m["kind"] == "employee"] == ["Вопрос", "Новое сообщение"]
    assert len([m for m in history if m["kind"] == "hr"]) == 1


def test_max_command_registration_matches_native_contract(runtime):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"commands": PUBLIC_COMMANDS})

    transport = MAXClient(
        runtime.settings,
        client=httpx.Client(base_url="https://platform-api2.max.ru", transport=httpx.MockTransport(handler)),
    )
    assert transport.set_commands(PUBLIC_COMMANDS)["commands"] == PUBLIC_COMMANDS
    assert requests[0].method == "PATCH" and requests[0].url.path == "/me/commands"
    assert json.loads(requests[0].content)["commands"] == PUBLIC_COMMANDS
    assert any(c["name"] == "hr" and "HR" in c["description"] for c in PUBLIC_COMMANDS)
    transport.close()


def test_legacy_hr_history_and_intro_are_migrated(runtime, monkeypatch):
    person = employee(runtime)
    scenario_id = scenario(runtime, steps=[{"id": "introduction", "text": "Расскажите рекрутеру о себе"}])
    Worker(runtime).tick()
    with runtime.db.transaction() as session:
        ticket = tickets.open_ticket(session, person, "Вопрос", {"question": "Первый вопрос"}, "legacy")
        tickets.employee_message(session, person, ticket, "Дополнение", "legacy-add")
        tickets.reply(
            session, session.get(Identity, "hr-a"), person, ticket, "Исторический ответ", "legacy-reply"
        )
    monkeypatch.setenv("KORSIK_DATABASE_URL", runtime.settings.database_url)
    monkeypatch.setenv("KORSIK_BOOTSTRAP_TOKEN", runtime.settings.bootstrap_token)
    monkeypatch.setenv("KORSIK_DATA_DIR", str(runtime.settings.data_dir))
    monkeypatch.setenv("KORSIK_ENVIRONMENT", "test")
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    runtime.db.engine.dispose()
    command.stamp(config, "head")
    command.downgrade(config, "0002")
    with runtime.db.engine.begin() as connection:
        connection.execute(text("UPDATE tickets SET human_mode=:value"), {"value": True})
    command.upgrade(config, "head")
    command.check(config)
    with runtime.db.transaction() as session:
        history = list(session.scalars(select(HRMessage).order_by(HRMessage.id)))
        assert len(history) == 3 and any(m.text == "Исторический ответ" for m in history)
        assert not session.scalar(select(Ticket)).human_mode
        assert session.get(Scenario, scenario_id).definition["steps"][0]["reply_to"] == "hr"
        assert session.get(Employee, "e1").dialogue["awaiting_hr_message"]["step_id"]
