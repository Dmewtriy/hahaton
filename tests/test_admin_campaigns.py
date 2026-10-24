from conftest import auth, employee, event, scenario
from sqlalchemy import select

from korsik.models import Employee, ScheduledStep, SurveyRun
from korsik.worker import Worker


def template(client, questions=None):
    response = client.post(
        "/api/admin/surveys",
        headers=auth("hr-a-key"),
        json={
            "title": "Как дела?",
            "branch_codes": ["A"],
            "questions": questions
            or [{"id": "rating", "text": "Оценка", "kind": "rating", "comment_below": 8}],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def draft(client, template_id=None, audience=None, **changes):
    response = client.post(
        "/api/admin/campaigns",
        headers=auth("hr-a-key"),
        json={
            "title": "Проверка настроения",
            "kind": "survey" if template_id else "message",
            "text": "Здравствуйте, {name}!",
            "template_id": template_id,
            "audience": audience or {"mode": "all"},
            **changes,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def publish(client, entry):
    preview = client.post("/api/admin/campaigns/preview", headers=auth("hr-a-key"), json=entry["audience"])
    assert preview.status_code == 200, preview.text
    return client.post(
        f"/api/admin/campaigns/{entry['id']}/publish",
        headers=auth("hr-a-key"),
        json={"audience_hash": preview.json()["audience_hash"], "revision": entry["revision"]},
    )


def test_survey_template_crud_clone_and_optimistic_revision(client, runtime):
    item = template(client)
    updated = client.put(
        f"/api/admin/surveys/{item['id']}",
        headers=auth("hr-a-key"),
        json={
            "title": "Обновлённый опрос",
            "branch_codes": ["A"],
            "revision": 1,
            "questions": [{"id": "q", "text": "Что улучшить?", "kind": "text"}],
        },
    )
    assert updated.status_code == 200 and updated.json()["revision"] == 2
    assert (
        client.put(
            f"/api/admin/surveys/{item['id']}",
            headers=auth("hr-a-key"),
            json={
                "title": "Conflict",
                "branch_codes": ["A"],
                "revision": 1,
                "questions": [{"id": "q", "text": "Что улучшить?", "kind": "text"}],
            },
        ).status_code
        == 409
    )
    cloned = client.post(f"/api/admin/surveys/{item['id']}/clone", headers=auth("hr-a-key"))
    assert cloned.status_code == 201 and cloned.json()["id"] != item["id"]
    assert client.delete(f"/api/admin/surveys/{item['id']}", headers=auth("hr-a-key")).status_code == 200
    assert (
        client.post(f"/api/admin/surveys/{item['id']}/restore", headers=auth("hr-a-key")).status_code == 200
    )


def test_campaign_audience_is_scoped_and_snapshot_is_immutable(client, runtime):
    employee(runtime)
    employee(runtime, emp_id="e2")
    employee(runtime, emp_id="outside", branch="B")
    item = template(client)
    entry = draft(client, item["id"])
    preview = client.post(
        "/api/admin/campaigns/preview", headers=auth("hr-a-key"), json={"mode": "all"}
    ).json()
    assert preview["count"] == 2
    assert (
        client.post(
            "/api/admin/campaigns/preview",
            headers=auth("hr-a-key"),
            json={"mode": "employees", "employee_ids": ["outside"]},
        ).status_code
        == 403
    )
    assert publish(client, entry).status_code == 200
    assert publish(client, entry).status_code == 409
    client.put(
        f"/api/admin/surveys/{item['id']}",
        headers=auth("hr-a-key"),
        json={
            "title": "Новая редакция",
            "branch_codes": ["A"],
            "revision": 1,
            "questions": [{"id": "changed", "text": "Другая редакция", "kind": "text"}],
        },
    )
    Worker(runtime).tick()
    with runtime.db.transaction() as session:
        runs = list(session.scalars(select(SurveyRun)))
        assert len(runs) == 2 and all(run.definition["questions"][0]["id"] == "rating" for run in runs)
    event(runtime, "8")
    result = client.get(f"/api/admin/campaigns/{entry['id']}", headers=auth("hr-a-key")).json()
    assert result["progress"]["completed"] == 1
    assert len(result["answers"]) == 1
    assert result["question_statistics"] == [{"question_id": "rating", "answer_count": 1, "average": 8.0}]


def test_campaign_does_not_reveal_transferred_employee_data(client, runtime):
    employee(runtime)
    entry = draft(client)
    assert publish(client, entry).status_code == 200
    with runtime.db.transaction() as session:
        session.get(Employee, "e1").branch_code = "B"
    assert client.get(f"/api/admin/campaigns/{entry['id']}", headers=auth("hr-a-key")).status_code == 403


def test_campaign_publish_requires_reviewed_members_and_revision(client, runtime):
    employee(runtime)
    entry = draft(client)
    preview = client.post(
        "/api/admin/campaigns/preview", headers=auth("hr-a-key"), json={"mode": "all"}
    ).json()
    employee(runtime, emp_id="late")
    response = client.post(
        f"/api/admin/campaigns/{entry['id']}/publish",
        headers=auth("hr-a-key"),
        json={"audience_hash": preview["audience_hash"], "revision": entry["revision"]},
    )
    assert response.status_code == 409
    assert publish(client, entry).status_code == 200


def test_campaign_survives_hire_date_reschedule_and_has_separate_queue(client, runtime):
    employee(runtime)
    scenario(runtime)
    Worker(runtime).tick()
    entry = draft(client)
    assert publish(client, entry).status_code == 200
    Worker(runtime).tick()
    with runtime.db.transaction() as session:
        step = session.scalar(select(ScheduledStep).where(ScheduledStep.campaign_id == entry["id"]))
        assert step.status == "queued"
        step_id = step.id
    assert (
        client.patch(
            "/api/employees/e1/dates", headers=auth("hr-a-key"), json={"hire_date": "2026-11-01"}
        ).status_code
        == 200
    )
    with runtime.db.transaction() as session:
        assert session.get(ScheduledStep, step_id).status == "queued"
    event(runtime, "8")
    with runtime.db.transaction() as session:
        assert session.get(ScheduledStep, step_id).status == "dispatched"


def test_campaign_cancel_stops_active_survey_without_losing_answers(client, runtime):
    employee(runtime)
    item = template(
        client,
        [{"id": "a", "text": "Первый", "kind": "rating"}, {"id": "b", "text": "Второй", "kind": "text"}],
    )
    entry = draft(client, item["id"])
    assert publish(client, entry).status_code == 200
    Worker(runtime).tick()
    event(runtime, "8")
    assert (
        client.post(f"/api/admin/campaigns/{entry['id']}/cancel", headers=auth("hr-a-key")).status_code == 200
    )
    detail = client.get(f"/api/admin/campaigns/{entry['id']}", headers=auth("hr-a-key")).json()
    assert detail["status"] == "cancelled" and len(detail["answers"]) == 1
    with runtime.db.transaction() as session:
        assert session.scalar(select(SurveyRun)).status == "cancelled"


def test_department_and_single_employee_audiences(client, runtime):
    headers = auth("hr-a-key")
    person = client.post(
        "/api/admin/employees",
        headers=headers,
        json={
            "name": "A",
            "branch_code": "A",
            "department": "R&D",
            "recruiter_id": "hr-a",
            "hire_date": "2026-10-26",
        },
    ).json()
    for audience in [
        {"mode": "departments", "department_ids": [person["department_id"]]},
        {"mode": "employees", "employee_ids": [person["id"]]},
    ]:
        result = client.post("/api/admin/campaigns/preview", headers=headers, json=audience)
        assert result.status_code == 200 and result.json()["count"] == 1
