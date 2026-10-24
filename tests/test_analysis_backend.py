from datetime import date, datetime

import pytest
from conftest import auth, clock, employee, event, scenario
from sqlalchemy import func, select

from korsik import documents, onboarding
from korsik.digests import create_snapshot
from korsik.ml import IndexResult
from korsik.models import (
    AssignmentHistory,
    Branch,
    Document,
    Employee,
    FeedbackAnalysis,
    Identity,
    MeetingSignal,
    Outbox,
    ScheduledStep,
    SurveyAnswer,
    SurveyRun,
    Ticket,
)
from korsik.worker import Worker


def campaign(client, *, template=None, expires=None, due=None):
    body = {
        "title": "Общая активность",
        "kind": "survey" if template else "message",
        "text": "Привет",
        "template_id": template,
        "audience": {"mode": "all"},
        "expires_at": expires,
        "due_at": due,
    }
    response = client.post("/api/admin/campaigns", headers=auth("hr-a-key"), json=body)
    assert response.status_code == 201, response.text
    created = response.json()
    preview = client.post(
        "/api/admin/campaigns/preview", headers=auth("hr-a-key"), json=body["audience"]
    ).json()
    published = client.post(
        f"/api/admin/campaigns/{created['id']}/publish",
        headers=auth("hr-a-key"),
        json={"revision": created["revision"], "audience_hash": preview["audience_hash"]},
    )
    assert published.status_code == 200, published.text
    return created["id"]


def upload(client, name="one.txt"):
    response = client.post(
        "/api/documents",
        headers=auth("hr-a-key"),
        data={"title": "Регламент", "branch_codes": '["A"]'},
        files={"file": (name, b"regulation")},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_end_of_probation_stops_only_onboarding_and_preserves_general_work(client, runtime):
    employee(runtime, hire=date(2026, 1, 1))
    scenario(runtime)
    campaign_id = campaign(client)
    Worker(runtime).tick()
    with runtime.db.transaction() as session:
        emp = session.get(Employee, "e1")
        assert emp.onboarding_status == "completed" and emp.active
        assert session.get(Identity, emp.identity_id).active
        assert not session.scalar(select(SurveyRun))
        general = session.scalar(select(ScheduledStep).where(ScheduledStep.campaign_id == campaign_id))
        assert general.status == "dispatched"
    event(runtime, "/hr После адаптации HR доступен")
    event(runtime, "/ask вопрос после адаптации")
    with runtime.db.transaction() as session:
        assert session.scalar(select(Ticket))
        assert any("После адаптации" in t.context.get("message", "") for t in session.scalars(select(Ticket)))
    assert client.get("/api/me", headers=auth("key-e1")).status_code == 200


def test_final_day_inclusive_and_employee_local_timezone(runtime):
    employee(runtime, hire=date(2026, 7, 23))
    clock(runtime, "2026-10-23T20:59:59+00:00")
    with runtime.db.transaction() as session:
        emp = session.get(Employee, "e1")
        assert onboarding.available(session, emp)
    clock(runtime, "2026-10-23T21:00:00+00:00")
    Worker(runtime).schedule()
    with runtime.db.transaction() as session:
        assert session.get(Employee, "e1").onboarding_status == "completed"


def test_existing_survey_survives_natural_end_and_campaign_waits_its_turn(runtime, client):
    employee(runtime, hire=date(2026, 7, 23))
    scenario(runtime)
    Worker(runtime).tick()
    event(runtime, "7")  # Unfinished conditional comment.
    clock(runtime, "2026-10-24T06:00:00+00:00")
    campaign(client)
    Worker(runtime).tick()
    with runtime.db.transaction() as session:
        assert session.scalar(select(SurveyRun)).status == "active"
        assert session.scalar(select(SurveyAnswer)).value["answer"] == 7
        assert session.scalar(
            select(Outbox.id).where(Outbox.dedupe_key.like("reminder:%"), Outbox.status == "sent")
        )
        assert (
            session.scalar(select(ScheduledStep).where(ScheduledStep.campaign_id.is_not(None))).status
            == "queued"
        )
    event(runtime, "Комментарий после окончания адаптации")
    with runtime.db.transaction() as session:
        assert session.scalar(select(SurveyRun)).status == "completed"
        assert (
            session.scalar(select(ScheduledStep).where(ScheduledStep.campaign_id.is_not(None))).status
            == "dispatched"
        )


def test_final_delivery_rechecks_end_even_without_scheduler(runtime):
    employee(runtime, hire=date(2026, 7, 23))
    scenario(runtime, steps=[{"id": "hello", "text": "adaptation", "schedule": {"at": "09:00"}}])
    worker = Worker(runtime)
    worker.schedule()
    while worker.process_one():
        pass
    clock(runtime, "2026-10-24T06:00:00+00:00")
    while worker.deliver_one():
        pass
    with runtime.db.transaction() as session:
        assert session.scalar(select(Outbox).where(Outbox.dedupe_key.like("step:%"))).status == "cancelled"


def test_hr_can_stop_resume_onboarding_separately_and_employee_cannot(client, runtime):
    employee(runtime)
    scenario(runtime)
    url = "/api/admin/employees/e1/onboarding"
    assert client.post(url, headers=auth("key-e1"), json={"action": "stop"}).status_code == 403
    assert client.post(url, headers=auth("manager-key"), json={"action": "stop"}).status_code == 403
    stopped = client.post(url, headers=auth("hr-a-key"), json={"action": "stop"})
    assert stopped.status_code == 200 and stopped.json()["active"]
    assert stopped.json()["onboarding_status"] == "stopped"
    assert (
        client.post(url, headers=auth("hr-a-key"), json={"action": "resume"}).json()["onboarding_status"]
        == "active"
    )
    clock(runtime, "2027-02-01T00:00:00+00:00")
    assert client.post(url, headers=auth("hr-a-key"), json={"action": "resume"}).status_code == 409


def test_skip_past_is_explicit_branch_policy_and_does_not_remove_future(runtime):
    employee(runtime)
    with runtime.db.transaction() as session:
        session.get(Branch, "A").late_join_policy = "skip_past"
    scenario(
        runtime,
        steps=[
            {"id": "past", "text": "past", "schedule": {"days": -1}},
            {"id": "next", "text": "next", "schedule": {"days": 1}},
        ],
    )
    with runtime.db.transaction() as session:
        assert [s.step_key for s in session.scalars(select(ScheduledStep))] == ["next"]


def flagged_answer(runtime):
    scenario(runtime, questions=[{"id": "feedback", "text": "Как дела?", "kind": "text"}])
    Worker(runtime).tick()
    event(runtime, "DEMO:RED_FLAG confidential comment")


def test_meeting_recommendation_is_once_private_and_manager_only(client, runtime):
    employee(runtime)
    flagged_answer(runtime)
    Worker(runtime).tick()
    with runtime.db.transaction() as session:
        signal = session.scalar(select(MeetingSignal))
        assert signal.manager_id == "manager"
        assert session.scalar(select(func.count()).select_from(MeetingSignal)) == 1
        notifications = list(session.scalars(select(Outbox).where(Outbox.dedupe_key.like("meeting:%"))))
        assert len(notifications) == 1 and notifications[0].recipient_id == "manager"
        assert "confidential" not in notifications[0].body["text"]
        assert session.scalar(select(FeedbackAnalysis)).processor_version == "stub/1"
    result = client.get("/api/workspace/meetings", headers=auth("manager-key")).json()
    assert len(result) == 1 and "analysis" not in result[0] and "answer" not in result[0]
    assert client.get("/api/workspace/meetings", headers=auth("hr-b-key")).json() == []
    assert client.get("/api/workspace/meetings", headers=auth("key-e1")).status_code == 403


def test_unassigned_meeting_routes_when_manager_appears(runtime):
    employee(runtime)
    with runtime.db.transaction() as session:
        session.get(Employee, "e1").manager_id = None
    flagged_answer(runtime)
    with runtime.db.transaction() as session:
        assert session.scalar(select(MeetingSignal)).status == "awaiting_manager"
        assert session.scalar(select(Ticket)).recruiter_id == "hr-a"
        session.get(Employee, "e1").manager_id = "manager"
    Worker(runtime).tick()
    with runtime.db.transaction() as session:
        assert session.scalar(select(MeetingSignal)).status == "delivered"


def test_revoked_manager_cannot_receive_queued_meeting(runtime):
    employee(runtime)
    scenario(runtime, questions=[{"id": "t", "text": "Текст", "kind": "text"}])
    Worker(runtime).tick()
    with runtime.db.transaction() as session:
        from korsik.worker import ingest

        ingest(
            session,
            {"event_id": "feedback", "kind": "message", "max_user_id": "max-e1", "text": "DEMO:RED_FLAG"},
        )
    worker = Worker(runtime)
    while worker.process_one():
        pass
    with runtime.db.transaction() as session:
        session.get(Identity, "manager").roles = []
    while worker.deliver_one():
        pass
    with runtime.db.transaction() as session:
        notification = session.scalar(select(Outbox).where(Outbox.dedupe_key.like("meeting:%")))
        assert notification.status == "cancelled"


def test_document_replacement_keeps_old_until_ready_and_supports_rollback(client, runtime, monkeypatch):
    emp = employee(runtime)
    old_id = upload(client)
    Worker(runtime).tick()
    response = client.post(
        f"/api/admin/documents/{old_id}/version",
        headers=auth("hr-a-key"),
        files={"file": ("new.txt", b"new")},
    )
    new_id = response.json()["id"]
    with runtime.db.transaction() as session:
        assert [d.id for d in documents.allowed_documents(session, emp)] == [old_id]
    monkeypatch.setattr(
        runtime.ml,
        "index",
        lambda doc, path: IndexResult(status="failed", external_id=doc.id, processor_version="test/1"),
    )
    Worker(runtime).tick()
    rows = client.get("/api/admin/documents", headers=auth("hr-a-key")).json()
    new = next(d for d in rows if d["id"] == new_id)
    assert new["status"] == "failed" and new["last_error"]
    with runtime.db.transaction() as session:
        assert [d.id for d in documents.allowed_documents(session, emp)] == [old_id]
    monkeypatch.setattr(
        runtime.ml,
        "index",
        lambda doc, path: IndexResult(status="ready", external_id=doc.id, processor_version="test/1"),
    )
    assert client.post(f"/api/documents/{new_id}/reindex", headers=auth("hr-a-key")).status_code == 200
    Worker(runtime).tick()
    with runtime.db.transaction() as session:
        assert [d.id for d in documents.allowed_documents(session, emp)] == [new_id]
        revision = session.get(Document, old_id).revision
    assert (
        client.post(
            f"/api/admin/documents/{old_id}/publish", headers=auth("hr-a-key"), json={"revision": revision}
        ).status_code
        == 200
    )
    with runtime.db.transaction() as session:
        assert [d.id for d in documents.allowed_documents(session, emp)] == [old_id]


def test_manual_document_publication_and_scope_checks(client, runtime):
    old = upload(client)
    assert (
        client.put(
            f"/api/admin/documents/{old}/publication",
            headers=auth("hr-a-key"),
            json={"auto_publish": False, "revision": 1},
        ).status_code
        == 200
    )
    Worker(runtime).tick()
    with runtime.db.transaction() as session:
        doc = session.get(Document, old)
        assert doc.status == "ready_stub" and not doc.published
        revision = doc.revision
    url = f"/api/admin/documents/{old}/publish"
    assert client.post(url, headers=auth("hr-b-key"), json={"revision": revision}).status_code == 403
    assert client.post(url, headers=auth("manager-key"), json={"revision": revision}).status_code == 403
    assert client.post(url, headers=auth("hr-a-key"), json={"revision": revision - 1}).status_code == 409
    assert client.post(url, headers=auth("hr-a-key"), json={"revision": revision}).status_code == 200


def test_quiz_scoring_deadline_and_repeat_keep_history(client, runtime):
    employee(runtime)
    template = client.post(
        "/api/admin/surveys",
        headers=auth("hr-a-key"),
        json={
            "title": "Тест",
            "branch_codes": ["A"],
            "kind": "quiz",
            "pass_percent": 75,
            "questions": [
                {
                    "id": "one",
                    "text": "Первый",
                    "kind": "single",
                    "options": [{"id": "yes", "label": "Да"}, {"id": "no", "label": "Нет"}],
                    "correct_options": ["yes"],
                    "points": 2,
                },
                {
                    "id": "many",
                    "text": "Второй",
                    "kind": "multiple",
                    "options": [{"id": "x", "label": "Икс"}, {"id": "y", "label": "Игрек"}],
                    "correct_options": ["x", "y"],
                    "points": 1,
                },
            ],
        },
    )
    assert template.status_code == 201, template.text
    template_id = template.json()["id"]
    first = campaign(client, template=template_id)
    Worker(runtime).tick()
    event(runtime, "1")
    event(runtime, "1")
    event(runtime, "2")
    event(runtime, "готово")
    result = client.get(f"/api/admin/campaigns/{first}", headers=auth("hr-a-key")).json()
    assert result["recipients"][0]["result"] == {
        "points": 3,
        "maximum": 3,
        "percent": 100.0,
        "pass_percent": 75,
        "passed": True,
    }
    second = campaign(client, template=template_id, expires="2026-10-24T06:00:00Z")
    Worker(runtime).tick()
    event(runtime, "2")
    clock(runtime, "2026-10-24T06:00:00+00:00")
    Worker(runtime).tick()
    data = client.get(f"/api/admin/campaigns/{second}", headers=auth("hr-a-key")).json()
    assert data["recipients"][0]["status"] == "expired" and data["recipients"][0]["result"] is None
    with runtime.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(SurveyAnswer)) == 3
        assert session.scalar(select(SurveyRun).where(SurveyRun.status == "completed")).result["passed"]
    manager = client.get("/api/employees/e1/surveys", headers=auth("manager-key")).json()
    assert all("answers" not in r and "result" not in r for r in manager)


def test_quiz_validates_correct_answers_and_rejects_invalid_deadlines(client, runtime):
    employee(runtime)
    response = client.post(
        "/api/admin/surveys",
        headers=auth("hr-a-key"),
        json={
            "title": "Bad",
            "kind": "quiz",
            "branch_codes": ["A"],
            "questions": [{"id": "q", "text": "Text", "kind": "text"}],
        },
    )
    assert response.status_code == 422
    response = client.post(
        "/api/admin/campaigns",
        headers=auth("hr-a-key"),
        json={
            "title": "Bad",
            "text": "Bad",
            "kind": "message",
            "audience": {"mode": "all"},
            "due_at": "2026-10-24T09:00:00Z",
            "expires_at": "2026-10-24T08:00:00Z",
        },
    )
    assert response.status_code == 422


def test_manager_rule_auto_assignment_and_history_and_role_boundaries(client, runtime):
    data = {"recruiter_id": "hr-a", "branch_code": "A", "manager_id": "manager"}
    assert client.put("/api/admin/manager-rules", headers=auth("hr-b-key"), json=data).status_code == 403
    assert client.put("/api/admin/manager-rules", headers=auth("manager-key"), json=data).status_code == 403
    assert client.put("/api/admin/manager-rules", headers=auth("hr-a-key"), json=data).status_code == 200
    created = client.post(
        "/api/admin/employees",
        headers=auth("hr-a-key"),
        json={
            "name": "New",
            "branch_code": "A",
            "department": "Operations",
            "recruiter_id": "hr-a",
            "hire_date": "2026-10-26",
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["manager_id"] == "manager"
    unassigned = client.post(
        "/api/admin/employees",
        headers=auth("hr-a-key"),
        json={
            "name": "Unassigned",
            "branch_code": "A",
            "department": "Operations",
            "recruiter_id": "hr-a",
            "hire_date": "2026-10-26",
            "auto_assign_manager": False,
        },
    )
    assert unassigned.status_code == 201 and unassigned.json()["manager_id"] is None
    with runtime.db.transaction() as session:
        assert session.scalar(select(AssignmentHistory)).current["manager_id"] == "manager"


def test_manager_workspace_is_read_only_and_other_roles_cannot_enter(client, runtime):
    employee(runtime)
    employee(runtime, emp_id="e2", branch="B")
    assert client.post("/api/admin/auth/local", json={"api_key": "manager-key"}).status_code == 200
    assert client.get("/api/me", headers=auth("manager-key")).json()["is_admin"] is False
    assert client.get("/api/me", headers=auth("manager-key")).json()["can_use_workspace"] is True
    data = client.get("/api/workspace/team", headers=auth("manager-key")).json()
    assert [e["id"] for e in data["employees"]] == ["e1"]
    assert "risks" not in data["totals"]
    assert client.get("/api/admin/employees", headers=auth("manager-key")).status_code == 403
    assert client.get("/api/workspace/team", headers=auth("key-e1")).status_code == 403
    assert client.post("/api/admin/auth/local", json={"api_key": "key-e1"}).status_code == 403


def test_digest_provenance_deduplication_and_access_recheck(client, runtime):
    employee(runtime)
    employee(runtime, emp_id="e2", branch="B")
    flagged_answer(runtime)
    with runtime.db.transaction() as session:
        end = datetime.fromisoformat("2026-10-24T00:00:00+00:00")
        actor = session.get(Identity, "head")
        first = create_snapshot(session, actor, end, "digest-test")
        second = create_snapshot(session, actor, end, "digest-test")
        assert first.id == second.id
        assert first.report["sample"]["answers"] == 1
        assert first.report["sample"]["responding_employees"] == 1
        assert first.provenance["employee_ids"] == ["e1"]
        digest_id = first.id
    url = f"/api/workspace/digests/{digest_id}"
    assert client.get(url, headers=auth("head-key")).status_code == 200
    assert client.get(url, headers=auth("hr-a-key")).status_code == 403
    assert client.get(url, headers=auth("manager-key")).status_code == 403
    with runtime.db.transaction() as session:
        session.get(Employee, "e1").branch_code = "B"
    assert client.get(url, headers=auth("head-key")).status_code == 403


def test_external_map_search_uses_only_workplace_and_does_not_go_to_rag(runtime, monkeypatch):
    employee(runtime)
    monkeypatch.setattr(runtime.ml, "answer", lambda _: pytest.fail("/places must not invoke RAG"))
    event(runtime, "/places кафе")
    with runtime.db.transaction() as session:
        session.get(Branch, "A").workplace = {"city": "Новосибирск", "street": "Ленина", "building": "1"}
    event(runtime, "/places кафе", event_id="places-address")
    with runtime.db.transaction() as session:
        item = session.scalar(select(Outbox).where(Outbox.dedupe_key == "places:places-address"))
        assert "Новосибирск" in item.body["text"]
        assert item.body["buttons"][0]["url"].startswith("https://yandex.ru/maps/?text=")
        assert not session.scalar(select(Ticket))


@pytest.mark.parametrize("change", ["branch", "deadline"])
def test_campaign_access_and_deadline_rechecked_before_final_delivery(client, runtime, change):
    employee(runtime)
    campaign_id = campaign(client, expires="2026-10-24T06:00:00Z")
    worker = Worker(runtime)
    worker.schedule()
    while worker.process_one():
        pass
    if change == "branch":
        with runtime.db.transaction() as session:
            session.get(Employee, "e1").branch_code = "B"
    else:
        clock(runtime, "2026-10-24T06:00:00+00:00")
    while worker.deliver_one():
        pass
    with runtime.db.transaction() as session:
        step = session.scalar(select(ScheduledStep).where(ScheduledStep.campaign_id == campaign_id))
        item = session.scalar(select(Outbox).where(Outbox.dedupe_key == "step:" + step.id))
        assert item.status == "cancelled"


def test_signed_manager_launch_has_no_hr_privileges(client, runtime):
    from test_max_webapp_auth import BOT_TOKEN, launch

    runtime.settings.max_token = BOT_TOKEN
    with runtime.db.transaction() as session:
        session.get(Identity, "manager").max_user_id = "104"
    response = client.post("/api/admin/auth/max", json={"init_data": launch(user_id=104)})
    assert response.status_code == 200
    token = response.json()["session_token"]
    assert client.get("/api/workspace/team", headers=auth(token)).status_code == 200
    assert client.get("/api/admin/employees", headers=auth(token)).status_code == 403
    with runtime.db.transaction() as session:
        session.get(Identity, "manager").roles = []
    assert client.get("/api/workspace/team", headers=auth(token)).status_code == 403


def test_no_data_digest_records_empty_sample_and_clear_message(runtime):
    with runtime.db.transaction() as session:
        head = session.get(Identity, "head")
        end = datetime.fromisoformat("2026-10-24T00:00:00+00:00")
        snapshot = create_snapshot(session, head, end, "empty-digest")
        assert snapshot.report["sample"]["answers"] == 0
        assert snapshot.provenance["answers"] == []
        assert "новых ответов и вопросов нет" in snapshot.text


def test_recruiter_scope_revocation_cancels_queued_private_notifications(runtime):
    employee(runtime)
    from korsik.tickets import open_ticket

    with runtime.db.transaction() as session:
        emp = session.get(Employee, "e1")
        open_ticket(session, emp, "Private", {"message": "private employee text"}, "private-test")
        session.get(Identity, "hr-a").branch_codes = ["B"]
    worker = Worker(runtime)
    while worker.deliver_one():
        pass
    with runtime.db.transaction() as session:
        item = session.scalar(select(Outbox).where(Outbox.dedupe_key.like("ticket:%:notify")))
        assert item.status == "cancelled"


def test_deadline_closes_assignment_before_employee_accepts_invitation(client, runtime):
    employee(runtime, bound=False)
    campaign_id = campaign(client, expires="2026-10-24T06:00:00Z")
    clock(runtime, "2026-10-24T07:00:00+00:00")
    Worker(runtime).tick()
    data = client.get(f"/api/admin/campaigns/{campaign_id}", headers=auth("hr-a-key")).json()
    assert data["status"] == "expired"
    assert data["recipients"][0]["status"] == "expired"
    with runtime.db.transaction() as session:
        assert not session.scalar(select(Outbox.id).where(Outbox.dedupe_key.like("step:%")))
