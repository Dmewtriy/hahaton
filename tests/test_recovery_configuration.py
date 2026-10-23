from datetime import timedelta

import pytest
from conftest import BOOTSTRAP, auth, clock, employee, event, scenario
from pydantic import ValidationError
from sqlalchemy import select

from korsik.calendar import now
from korsik.db import utcnow
from korsik.models import Job
from korsik.queue import enqueue
from korsik.schemas import BranchInput, ScenarioDefinition
from korsik.worker import Worker


def test_new_branch_and_scenario_via_api_without_code_change(client, runtime):
    created = client.post(
        "/api/identities",
        headers=auth(BOOTSTRAP),
        json={"name": "Global head", "roles": ["hr_head"], "branch_codes": ["*"]},
    ).json()
    headers = auth(created["api_token"])
    response = client.post(
        "/api/scenarios",
        headers=headers,
        json={
            "name": "New site",
            "version": 1,
            "steps": [{"id": "welcome", "text": "Здравствуйте, {name}", "schedule": {"working_day": 1}}],
        },
    )
    assert response.status_code == 201
    branch = client.put(
        "/api/branches/NEW",
        headers=headers,
        json={
            "code": "NEW",
            "name": "Новая площадка",
            "timezone": "Asia/Novosibirsk",
            "scenario_id": response.json()["id"],
            "resources": {"food": "Новый адрес"},
        },
    )
    assert branch.status_code == 200
    assert any(b["code"] == "NEW" for b in client.get("/api/branches", headers=headers).json())


def test_configuration_validation_rejects_invalid_or_unsafe_input():
    with pytest.raises(ValidationError):
        BranchInput(code="A", name="A", timezone="No/SuchZone")
    with pytest.raises(ValidationError):
        BranchInput(code="A", name="A", workdays=[])
    with pytest.raises(ValidationError):
        ScenarioDefinition.model_validate(
            {"name": "bad", "version": 1, "steps": [{"id": "x", "text": "{name.__class__}"}]}
        )
    with pytest.raises(ValidationError):
        ScenarioDefinition.model_validate(
            {
                "name": "bad",
                "version": 1,
                "steps": [{"id": "x", "text": "x", "kind": "survey", "survey_id": "unknown"}],
            }
        )


def test_expired_worker_lease_is_recovered(runtime):
    with runtime.db.transaction() as session:
        job = enqueue(session, "max_ack", {"callback_id": "a"}, "expired", now(session))
        job.status, job.lease_owner = "running", "dead-worker"
        job.lease_until = utcnow() - timedelta(seconds=5)
    Worker(runtime).process_one()
    with runtime.db.transaction() as session:
        job = session.scalar(select(Job).where(Job.dedupe_key == "expired"))
        assert job.status == "done" and job.attempts == 1


def test_failed_jobs_have_bounded_retries(runtime):
    with runtime.db.transaction() as session:
        enqueue(session, "unknown_kind", {}, "bad", now(session))
    for day in range(7):
        clock(runtime, f"2026-11-{day + 1:02}T00:00:00+00:00")
        Worker(runtime).process_one()
    with runtime.db.transaction() as session:
        job = session.scalar(select(Job).where(Job.dedupe_key == "bad"))
        assert job.status == "failed" and job.attempts == runtime.settings.max_job_attempts


def test_deactivated_branch_cancels_future_steps(client, runtime):
    employee(runtime)
    sid = scenario(runtime, steps=[{"id": "future", "text": "future", "schedule": {"days": 10}}])
    response = client.put(
        "/api/branches/A",
        headers=auth("hr-a-key"),
        json={"code": "A", "name": "A", "active": False, "scenario_id": sid},
    )
    assert response.status_code == 200
    clock(runtime, "2026-12-01T06:00:00+00:00")
    Worker(runtime).tick()
    assert not runtime.transport.deliveries


def test_revoked_employee_role_blocks_own_data(client, runtime):
    employee(runtime)
    from korsik.models import Identity

    with runtime.db.transaction() as session:
        session.get(Identity, "i-e1").roles = []
    assert client.get("/api/employees/e1", headers=auth("key-e1")).status_code == 403


def test_cross_employee_callback_cannot_answer_other_survey(runtime):
    employee(runtime)
    employee(runtime, emp_id="e2")
    scenario(runtime)
    Worker(runtime).tick()
    from korsik.models import SurveyAnswer, SurveyRun

    with runtime.db.transaction() as session:
        run_id = session.scalar(select(SurveyRun.id).where(SurveyRun.employee_id == "e2"))
    event(runtime, kind="callback", payload=f"a:{run_id}:0:8")
    with runtime.db.transaction() as session:
        assert not list(session.scalars(select(SurveyAnswer)))


def test_uncertain_delivery_requires_explicit_operator_decision(client, runtime):
    employee(runtime)
    from korsik.queue import send

    with runtime.db.transaction() as session:
        item = send(session, "i-e1", "Private message", "uncertain-test", now(session))
        item.status = "uncertain"
        item_id = item.id
    problems = client.get("/api/operations/deliveries", headers=auth(BOOTSTRAP)).json()
    assert "Private message" not in str(problems)
    url = f"/api/operations/deliveries/{item_id}/resolve"
    assert client.post(url, headers=auth(BOOTSTRAP), json={"action": "retry"}).status_code == 422
    assert client.post(url, headers=auth("hr-a-key"), json={"action": "mark_sent"}).status_code == 403
    assert client.post(url, headers=auth(BOOTSTRAP), json={"action": "mark_sent"}).status_code == 200
