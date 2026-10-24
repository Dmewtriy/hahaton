from datetime import timedelta

import pytest
from conftest import BOOTSTRAP, auth, clock, employee, event, scenario
from sqlalchemy import func, select

from korsik import documents, improvements, surveys
from korsik.admin_schemas import SurveyInput
from korsik.calendar import now
from korsik.extension_models import ImprovementObservation, ManagementEntry, ManagementTask
from korsik.models import Document, Employee, HelpSession, Identity, Job, Outbox, State, SurveyRun
from korsik.queue import send
from korsik.worker import Worker


@pytest.fixture(autouse=True)
def extension_configuration_admin(runtime):
    # Explicitly give the technical configurator global configuration scope, not an HR role.
    with runtime.db.transaction() as session:
        session.get(Identity, "bootstrap").branch_codes = ["*"]


def grant(client, assignee="hr-a", branch="A"):
    response = client.post(
        "/api/management/grants",
        headers=auth(BOOTSTRAP),
        json={
            "issuer_id": "head",
            "assignee_id": assignee,
            "branch_code": branch,
        },
    )
    assert response.status_code == 200, response.text


def task(client, key="create", **extra):
    response = client.post(
        "/api/management/tasks",
        headers=auth("head-key"),
        json={
            "assignee_id": "hr-a",
            "branch_code": "A",
            "title": "Уточнить инструкцию",
            "description": "Проверить доступы",
            "request_key": key,
            **extra,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def command(client, item, action, actor="hr-a-key", **extra):
    return client.post(
        f"/api/management/tasks/{item['id']}/commands",
        headers=auth(actor),
        json={
            "action": action,
            "revision": item["revision"],
            "request_key": action + str(item["revision"]),
            **extra,
        },
    )


def upload(client, purpose="personal_plan", **extra):
    return client.post(
        "/api/optional/attachments",
        headers=auth("hr-a-key"),
        data={
            "title": "План сотрудника",
            "purpose": purpose,
            "branch_code": "A",
            **({"employee_id": "e1"} if purpose == "personal_plan" else {}),
            **extra,
        },
        files={"file": ("plan.txt", b"Personal onboarding plan", "text/plain")},
    )


def planning(client, kind="meeting", **extra):
    return client.post(
        "/api/optional/planning",
        headers=auth("hr-a-key"),
        json={
            "kind": kind,
            "title": "Встреча адаптации",
            "branch_code": "A",
            "starts_at": "2026-10-25T09:00:00+03:00",
            "timezone": "Europe/Moscow",
            "audience": {"mode": "branches", "branch_codes": ["A"]},
            "status": "scheduled" if kind == "meeting" else "published",
            **extra,
        },
    )


def test_personal_plan_versions_access_and_exclusion_from_index(runtime, client):
    employee(runtime)
    employee(runtime, emp_id="e2")
    first = upload(client)
    assert first.status_code == 201, first.text
    second = upload(client)
    assert second.json()["version"] == 2
    assert (
        client.get("/api/optional/employees/e1/plan", headers=auth("key-e1")).json()["document"]["id"]
        == second.json()["id"]
    )
    assert client.get("/api/optional/employees/e1/plan", headers=auth("manager-key")).status_code == 200
    assert client.get("/api/optional/employees/e1/plan", headers=auth("key-e2")).status_code == 403
    assert (
        client.get(f"/api/documents/{second.json()['id']}/download", headers=auth("key-e2")).status_code
        == 403
    )
    with runtime.db.transaction() as session:
        emp = session.get(Employee, "e1")
        assert not documents.allowed_documents(session, emp)
        assert not list(session.scalars(select(Job).where(Job.kind == "index_document")))
        assert not session.get(Document, first.json()["id"]).published


def test_plan_cannot_become_knowledge_on_generic_version(runtime, client):
    employee(runtime)
    original = upload(client).json()
    response = client.post(
        f"/api/admin/documents/{original['id']}/version",
        headers=auth("hr-a-key"),
        files={"file": ("new.txt", b"new plan", "text/plain")},
    )
    assert response.status_code == 201, response.text
    assert response.json()["purpose"] == "personal_plan"
    assert response.json()["owner_employee_id"] == "e1"
    assert client.get("/api/documents", headers=auth("key-e1")).json() == []
    assert (
        client.post(f"/api/documents/{original['id']}/reindex", headers=auth("hr-a-key")).status_code == 422
    )


def test_plan_not_available_in_broadcast(runtime, client):
    employee(runtime)
    doc = upload(client).json()
    response = client.post(
        "/api/admin/campaigns",
        headers=auth("hr-a-key"),
        json={
            "title": "Не общий файл",
            "kind": "message",
            "text": "Привет",
            "document_ids": [doc["id"]],
            "audience": {"mode": "branches", "branch_codes": ["A"]},
        },
    )
    assert response.status_code == 422


def test_missing_plan_is_truthful_and_menu_preserves_survey(runtime):
    employee(runtime)
    scenario(runtime)
    Worker(runtime).tick()
    event(runtime, "4")
    with runtime.db.transaction() as session:
        run = session.scalar(select(SurveyRun))
        assert run.awaiting_comment
        run_id = run.id
        before = dict(session.get(Employee, "e1").dialogue)
    event(runtime, kind="callback", payload="nav:page:0")
    event(runtime, kind="callback", payload="nav:do:plan")
    with runtime.db.transaction() as session:
        run = session.get(SurveyRun, run_id)
        assert run.awaiting_comment and run.position == 0
        assert session.get(Employee, "e1").dialogue == before
        assert (
            session.scalar(select(Outbox).where(Outbox.dedupe_key.like("plan:%"))).body["document_ids"] == []
        )
    event(runtime, "У меня не работает доступ")
    with runtime.db.transaction() as session:
        assert session.get(SurveyRun, run_id).status == "completed"


def test_event_catalog_scope_and_history(runtime, client):
    employee(runtime)
    employee(runtime, emp_id="e2", branch="B")
    response = planning(client, "event")
    assert response.status_code == 201, response.text
    item = response.json()
    assert len(client.get("/api/optional/events", headers=auth("key-e1")).json()) == 1
    assert client.get("/api/optional/events", headers=auth("key-e2")).json() == []
    assert (
        client.get(f"/api/optional/planning/{item['id']}/history", headers=auth("hr-b-key")).status_code
        == 403
    )
    item["title"] = "Изменённое событие"
    item.pop("id")
    item.pop("organizer_id")
    edited = client.put(
        f"/api/optional/planning/{response.json()['id']}", headers=auth("hr-a-key"), json=item
    )
    assert edited.status_code == 200, edited.text
    assert (
        len(
            client.get(
                f"/api/optional/planning/{response.json()['id']}/history", headers=auth("hr-a-key")
            ).json()
        )
        == 2
    )


def test_reschedule_invalidates_draft_and_pending_delivery(runtime, client):
    employee(runtime)
    response = planning(client)
    assert response.status_code == 201, response.text
    item = response.json()
    draft = client.post(
        f"/api/optional/planning/{item['id']}/announcement", headers=auth("hr-a-key"), json={"revision": 1}
    ).json()
    assert draft["stored_status"] == "draft"
    preview = client.post(
        "/api/admin/campaigns/preview", headers=auth("hr-a-key"), json=draft["audience"]
    ).json()
    data = {k: v for k, v in item.items() if k not in {"id", "organizer_id"}}
    data["starts_at"] = "2026-10-26T09:00:00+03:00"
    assert (
        client.put(f"/api/optional/planning/{item['id']}", headers=auth("hr-a-key"), json=data).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/admin/campaigns/{draft['id']}/publish",
            headers=auth("hr-a-key"),
            json={
                "revision": draft["revision"],
                "audience_hash": preview["audience_hash"],
            },
        ).status_code
        == 409
    )


@pytest.mark.parametrize("token", ["manager-key", "key-e1", "hr-b-key"])
def test_planning_not_open_to_other_roles_or_scopes(runtime, client, token):
    employee(runtime)
    response = client.post(
        "/api/optional/planning",
        headers=auth(token),
        json={
            "kind": "event",
            "title": "Событие",
            "branch_code": "A",
            "audience": {"mode": "branches", "branch_codes": ["A"]},
        },
    )
    assert response.status_code == 403


@pytest.mark.parametrize("days", [[0], [31], [1.5], [True], ["2"]])
def test_reminders_are_strict_whole_days(days):
    with pytest.raises(ValueError):
        SurveyInput(
            title="Тест",
            branch_codes=["A"],
            questions=[{"id": "q", "text": "Оценка", "kind": "rating"}],
            reminder_days=days,
        )


def test_reminder_defaults_and_normalization():
    kwargs = {
        "title": "Тест",
        "branch_codes": ["A"],
        "questions": [{"id": "q", "text": "Оценка", "kind": "rating"}],
    }
    assert SurveyInput(**kwargs).reminder_days == []
    assert SurveyInput(**kwargs, reminder_days=[3, 1, 3]).reminder_days == [1, 3]


def test_reminder_outbox_rechecks_completed_run(runtime):
    employee(runtime)
    scenario(runtime)
    Worker(runtime).tick()
    with runtime.db.transaction() as session:
        run = session.scalar(select(SurveyRun))
        assert surveys.reminder_allowed(session, run.id)
        item = send(session, "i-e1", "Напоминание", "test-reminder", now(session))
        item.body = {**item.body, "reminder_run_id": run.id}
        run.status = "completed"
    Worker(runtime).deliver_one()
    with runtime.db.transaction() as session:
        assert (
            session.scalar(select(Outbox).where(Outbox.dedupe_key == "test-reminder")).status == "cancelled"
        )


def test_grant_required_and_role_not_equivalent(client):
    response = client.post(
        "/api/management/tasks",
        headers=auth("head-key"),
        json={
            "assignee_id": "hr-a",
            "branch_code": "A",
            "title": "Тест",
            "description": "Проверить",
            "request_key": "x",
        },
    )
    assert response.status_code == 403
    assert client.get("/api/management/tasks", headers=auth("manager-key")).status_code == 403
    grant(client)
    assert task(client)["status"] == "assigned"


def test_task_idempotency_conversation_and_read_state(runtime, client):
    grant(client)
    first = task(client)
    assert task(client)["id"] == first["id"]
    second = task(client, "second")
    assert first["conversation_id"] == second["conversation_id"]
    conv = first["conversation_id"]
    assert client.get("/api/management/summary", headers=auth("hr-a-key")).json()["unread_count"] == 2
    assert (
        client.post(
            f"/api/management/conversations/{conv}/read", headers=auth("hr-a-key"), json={"through_seq": 1}
        ).status_code
        == 200
    )
    assert client.get("/api/management/summary", headers=auth("hr-a-key")).json()["unread_count"] == 1
    assert (
        client.get(f"/api/management/tasks/{first['id']}", headers=auth("hr-a-key")).json()["status"]
        == "assigned"
    )
    with runtime.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(ManagementEntry)) == 2


def test_task_transitions_blocker_response_does_not_resume(client):
    grant(client)
    item = task(client)
    assert (
        command(
            client, item, "complete", actor="head-key", text="Не от HR", result_kind="process_adjusted"
        ).status_code
        == 403
    )
    blocked = command(client, item, "block", text="Нужны сведения").json()
    response = command(client, blocked, "answer_blocker", actor="head-key", text="Сведения готовы")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "blocked"
    resumed = command(client, response.json(), "resume").json()
    completed = command(
        client, resumed, "complete", text="Обновили порядок выдачи доступов", result_kind="process_adjusted"
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "completed"
    assert command(client, completed.json(), "reopen", actor="head-key").status_code == 422
    assert (
        command(client, completed.json(), "reopen", actor="head-key", text="Нужна проверка").json()["status"]
        == "in_progress"
    )


def test_stale_revision_and_payload_key_conflict(client):
    grant(client)
    item = task(client)
    accepted = command(client, item, "accept")
    assert accepted.status_code == 200
    assert command(client, item, "accept").status_code == 200
    assert command(client, item, "block", text="Старая версия").status_code == 409
    assert command(client, item, "accept", text="Другие данные с тем же ключом").status_code == 409


def test_scope_loss_hides_detail_and_unread(runtime, client):
    grant(client)
    item = task(client)
    with runtime.db.transaction() as session:
        session.get(Identity, "hr-a").branch_codes = ["B"]
    assert client.get(f"/api/management/tasks/{item['id']}", headers=auth("hr-a-key")).status_code == 404
    assert client.get("/api/management/summary", headers=auth("hr-a-key")).json()["unread_count"] == 0
    assert (
        client.get(
            f"/api/management/conversations/{item['conversation_id']}/entries", headers=auth("hr-a-key")
        ).status_code
        == 404
    )


def test_personal_plan_or_stub_cannot_complete_real_knowledge_task(runtime, client):
    employee(runtime)
    grant(client)
    item = task(client)
    plan = upload(client).json()
    assert (
        command(
            client,
            item,
            "complete",
            result_kind="knowledge_updated",
            text="Готово",
            document_id=plan["id"],
            document_version=1,
        ).status_code
        == 422
    )
    with runtime.db.transaction() as session:
        doc = documents.store(session, runtime.settings, "Инструкция", "kb.txt", b"KB content", ["A"], [])
        doc_id = doc.id
    Worker(runtime).tick()
    assert (
        command(
            client,
            item,
            "complete",
            result_kind="knowledge_updated",
            text="Готово",
            document_id=doc_id,
            document_version=1,
        ).status_code
        == 422
    )
    with runtime.db.transaction() as session:
        session.get(Document, doc_id).status = "ready"
    response = command(
        client,
        item,
        "complete",
        result_kind="knowledge_updated",
        text="Готово",
        document_id=doc_id,
        document_version=1,
    )
    assert response.status_code == 200, response.text


def report_policy(client):
    response = client.put(
        "/api/management/improvements/policy",
        headers=auth(BOOTSTRAP),
        json={
            "enabled": True,
            "mode": "demo",
            "approved_topics": ["access"],
            "min_employees": 3,
        },
    )
    assert response.status_code == 200, response.text


def add_help(runtime, employee_id, *, count=1, status="not_found", helpful=None):
    with runtime.db.transaction() as session:
        for i in range(count):
            session.add(
                HelpSession(
                    employee_id=employee_id,
                    question="Вопрос без цитаты в отчёте",
                    status="error",
                    processor_version="stub/1",
                    context={
                        "answer_status": status,
                        "mode": "demo",
                        "topics": ["access"],
                        "helpful": helpful,
                        "revision": 1,
                        "branch_code_at_event": "A",
                    },
                    created_at=now(session) - timedelta(hours=1),
                )
            )


def test_report_threshold_retry_and_no_raw_content(runtime, client):
    employee(runtime)
    report_policy(client)
    add_help(runtime, "e1", count=5)
    assert client.post("/api/management/improvements/reports", headers=auth("head-key")).json()["items"] == []
    employee(runtime, emp_id="e2")
    employee(runtime, emp_id="e3")
    add_help(runtime, "e2")
    add_help(runtime, "e3")
    response = client.post("/api/management/improvements/reports", headers=auth("head-key"))
    assert response.status_code == 201, response.text
    report = response.json()
    assert report["items"][0]["cases"] == 7
    assert report["items"][0]["employees"] == 3
    assert report["items"][0]["rate"] == 100
    assert report["items"][0]["denominator"] == 7
    assert "employee_id" not in response.text and "Вопрос без цитаты" not in response.text
    again = client.post("/api/management/improvements/reports", headers=auth("head-key")).json()
    assert again["items"][0]["id"] == report["items"][0]["id"]
    with runtime.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(ImprovementObservation)) == 7


@pytest.mark.parametrize(
    "status,helpful",
    [
        ("unavailable", None),
        ("blocked", None),
        ("clarification", None),
        ("answered", None),
        ("answered", True),
    ],
)
def test_technical_errors_and_missing_feedback_are_not_gaps(runtime, client, status, helpful):
    report_policy(client)
    for i in range(3):
        employee(runtime, emp_id="e" + str(i))
        add_help(runtime, "e" + str(i), status=status, helpful=helpful)
    assert client.post("/api/management/improvements/reports", headers=auth("head-key")).json()["items"] == []


def test_improvement_snooze_and_single_active_task(runtime, client):
    report_policy(client)
    grant(client)
    for i in range(3):
        employee(runtime, emp_id="e" + str(i))
        add_help(runtime, "e" + str(i), status="answered", helpful=False)
    report = client.post("/api/management/improvements/reports", headers=auth("head-key")).json()
    insight = report["items"][0]
    first = task(client, mode="demo", source_insight_id=insight["id"], source_report_id=report["id"])
    duplicate = client.post(
        "/api/management/tasks",
        headers=auth("head-key"),
        json={
            "assignee_id": "hr-a",
            "branch_code": "A",
            "title": "Дубль",
            "description": "Дубль",
            "request_key": "duplicate",
            "mode": "demo",
            "source_insight_id": insight["id"],
            "source_report_id": report["id"],
        },
    )
    assert duplicate.status_code == 409
    assert (
        client.post(
            f"/api/management/improvements/insights/{insight['id']}/snooze", headers=auth("head-key")
        ).status_code
        == 200
    )
    assert client.post("/api/management/improvements/reports", headers=auth("head-key")).json()["items"] == []
    complete = command(
        client, first, "complete", text="Исправили процесс", result_kind="process_adjusted"
    ).json()
    follow = client.get(
        f"/api/management/improvements/tasks/{complete['id']}/followup", headers=auth("head-key")
    )
    assert follow.json()["status"] == "Наблюдение продолжается"


def test_projection_older_revision_cannot_rollback(runtime):
    employee(runtime)
    with runtime.db.transaction() as session:
        values = {
            "employee_id": "e1",
            "branch_code": "A",
            "category": "none",
            "topic": "access",
            "topics": ["access"],
            "occurred_at": now(session),
            "mode": "demo",
            "terminal_status": "answered",
            "helpful": True,
            "question_key": None,
            "comment_present": False,
        }
        improvements.upsert(session, "logical", 2, values)
        session.flush()
        improvements.upsert(session, "logical", 1, {**values, "terminal_status": "not_found"})
        assert session.get(ImprovementObservation, "logical").terminal_status == "answered"


def test_flags_and_menu_authorization(runtime, client):
    employee(runtime)
    technical_menu = client.get("/api/menu", headers=auth(BOOTSTRAP)).json()
    assert "dashboard" not in {x["id"] for x in technical_menu}
    manager_menu = client.get("/api/menu", headers=auth("manager-key")).json()
    assert "admin" not in {x["id"] for x in manager_menu}
    actions = client.get("/api/menu", headers=auth("key-e1")).json()
    assert "plan" in {x["id"] for x in actions} and "dashboard" not in {x["id"] for x in actions}
    assert client.post("/api/menu/dashboard", headers=auth("key-e1")).status_code == 403
    runtime.settings.enable_optional = False
    from korsik.commands import configured_commands

    assert "plan" not in {x["name"] for x in configured_commands(runtime.settings)}
    assert client.get("/api/optional/events", headers=auth("key-e1")).status_code == 404
    event(runtime, kind="callback", payload="nav:do:plan")
    runtime.settings.enable_management = False
    assert client.get("/api/management/tasks", headers=auth("head-key")).status_code == 404


def test_plan_schedule_is_explicit_idempotent_and_uses_work_calendar(runtime, client):
    employee(runtime)
    upload(client)
    body = {"request_key": "plan-third-day", "third_workday": True}
    first = client.post("/api/optional/employees/e1/plan/send", headers=auth("hr-a-key"), json=body)
    assert first.status_code == 200, first.text
    assert first.json()["due_at"].startswith("2026-10-27T06:00")
    assert (
        client.post("/api/optional/employees/e1/plan/send", headers=auth("hr-a-key"), json=body).json()[
            "job_id"
        ]
        == first.json()["job_id"]
    )
    with runtime.db.transaction() as session:
        assert not session.scalar(select(Outbox).where(Outbox.dedupe_key.like("plan:%")))


def test_task_cancel_suppresses_old_notice_but_delivers_cancellation(runtime, client):
    grant(client)
    item = task(client)
    assert command(client, item, "cancel", actor="head-key", text="Потеряло актуальность").status_code == 200
    Worker(runtime).tick()
    with runtime.db.transaction() as session:
        entries = {x.id: x.kind for x in session.scalars(select(ManagementEntry))}
        boxes = list(session.scalars(select(Outbox).where(Outbox.dedupe_key.like("management:%"))))
        assert any(
            entries[b.body["management_entry_id"]] == "assigned" and b.status == "cancelled" for b in boxes
        )
        assert any(entries[b.body["management_entry_id"]] == "cancel" and b.status == "sent" for b in boxes)


def test_uncertain_management_notification_does_not_accept_or_retry(runtime, client):
    from korsik.max_client import DeliveryError

    grant(client)
    item = task(client)

    def fail(*args, **kwargs):
        raise DeliveryError("uncertain_test", uncertain=True)

    runtime.transport.send = fail
    Worker(runtime).deliver_one()
    with runtime.db.transaction() as session:
        box = session.scalar(select(Outbox).where(Outbox.dedupe_key.like("management:%")))
        assert box.status == "uncertain"
        assert session.get(ManagementTask, item["id"]).status == "assigned"
    assert not Worker(runtime).deliver_one()


def test_concurrent_creation_reuses_key_and_one_conversation(runtime, client):
    from concurrent.futures import ThreadPoolExecutor

    grant(client)
    body = {
        "assignee_id": "hr-a",
        "branch_code": "A",
        "title": "Одно поручение",
        "description": "Один запрос",
        "request_key": "concurrent",
    }

    def create(_):
        return client.post("/api/management/tasks", headers=auth("head-key"), json=body)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(create, range(2)))
    assert all(x.status_code == 201 for x in responses), [x.text for x in responses]
    assert len({x.json()["id"] for x in responses}) == 1


def test_process_rules_use_exact_version_and_distinct_denominator(runtime, client):
    from korsik.extension_schemas import ImprovementPolicy
    from korsik.models import SurveyAnswer

    for i in range(4):
        employee(runtime, emp_id="p" + str(i))
    scenario(runtime)
    Worker(runtime).tick()
    with runtime.db.transaction() as session:
        runs = list(session.scalars(select(SurveyRun).order_by(SurveyRun.employee_id)))
        definition = runs[0].definition
        config = ImprovementPolicy(
            enabled=True,
            mode="real",
            approved_by=["Владелец процесса"],
            approved_topics=["access"],
            process_topics=["access"],
            process_rules=[
                {
                    "survey_id": definition["id"],
                    "survey_version": improvements.signature(definition),
                    "question_id": "rating",
                    "topic": "access",
                    "below": 8,
                }
            ],
        )
        session.merge(State(key="improvement_policy", value=config.model_dump(mode="json")))
        for i, run in enumerate(runs):
            session.add(
                SurveyAnswer(
                    run_id=run.id,
                    question_id="rating",
                    value={"answer": 4 if i < 3 else 10},
                    created_at=now(session) - timedelta(minutes=1),
                )
            )
    report = client.post("/api/management/improvements/reports", headers=auth("head-key"))
    assert report.status_code == 201, report.text
    card = report.json()["items"][0]
    assert card["cases"] == 3 and card["employees"] == 3 and card["denominator"] == 4 and card["rate"] == 75
    assert card["topic"] == "access"


def test_report_modes_do_not_mix_and_revoked_policy_hides_snapshot(runtime, client):
    report_policy(client)
    for i in range(3):
        employee(runtime, emp_id="m" + str(i))
        add_help(runtime, "m" + str(i))
    report = client.post("/api/management/improvements/reports", headers=auth("head-key")).json()
    with runtime.db.transaction() as session:
        stored = session.get(State, "improvement_policy")
        stored.value = {**stored.value, "mode": "real", "approved_by": ["Владелец"]}
    assert client.post("/api/management/improvements/reports", headers=auth("head-key")).json()["items"] == []
    assert (
        client.get(
            "/api/management/improvements/reports/" + report["id"], headers=auth("head-key")
        ).status_code
        == 409
    )


def test_suppression_reason_survives_worker_and_is_visible_to_hr(runtime, client):
    employee(runtime)
    scenario(runtime)
    Worker(runtime).schedule()
    Worker(runtime).process_one()
    with runtime.db.transaction() as session:
        run = session.scalar(select(SurveyRun))
        assert run
        for box in session.scalars(select(Outbox)):
            box.status = "failed"
    clock(runtime, "2026-10-24T06:00:00+00:00")
    Worker(runtime).tick()
    detail = client.get("/api/admin/employees/e1", headers=auth("hr-a-key")).json()
    assert any(r["suppressed_reason"] for r in detail["reminders"])


def test_card_requires_image_and_delivers_the_selected_file(runtime, client):
    import base64

    employee(runtime)
    body = {
        "title": "Открытка",
        "kind": "message",
        "message_type": "first_day_card",
        "text": "С первым днём!",
        "audience": {"mode": "branches", "branch_codes": ["A"]},
    }
    assert client.post("/api/admin/campaigns", headers=auth("hr-a-key"), json=body).status_code == 422
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aF8sAAAAASUVORK5CYII="
    )
    image = client.post(
        "/api/optional/attachments",
        headers=auth("hr-a-key"),
        data={"title": "Открытка", "purpose": "card_image", "branch_code": "A"},
        files={"file": ("card.png", png, "image/png")},
    )
    assert image.status_code == 201, image.text
    body["document_ids"] = [image.json()["id"]]
    draft = client.post("/api/admin/campaigns", headers=auth("hr-a-key"), json=body).json()
    preview = client.post(
        "/api/admin/campaigns/preview", headers=auth("hr-a-key"), json=body["audience"]
    ).json()
    response = client.post(
        "/api/admin/campaigns/" + draft["id"] + "/publish",
        headers=auth("hr-a-key"),
        json={"revision": draft["revision"], "audience_hash": preview["audience_hash"]},
    )
    assert response.status_code == 200, response.text
    Worker(runtime).tick()
    assert any(
        item["attachments"] and item["attachments"][0]["type"] == "image"
        for item in runtime.transport.deliveries
    )


def test_invalid_timezone_is_validation_error(client):
    response = planning(client, timezone="No/Such_Zone")
    assert response.status_code == 422, response.text
