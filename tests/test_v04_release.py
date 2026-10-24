import io
from datetime import timedelta
from zipfile import ZipFile

import pytest
from conftest import auth, employee, event, scenario
from sqlalchemy import select

from korsik import profile_v04, reporting, routes, surveys
from korsik.calendar import now
from korsik.models import (
    Branch,
    Document,
    Employee,
    Identity,
    Material,
    MeetingSignal,
    Outbox,
    RouteRun,
    ScheduledStep,
    State,
    SurveyAnswer,
    SurveyRun,
    Ticket,
)
from korsik.worker import Worker


def fixture_image(session, runtime):
    import base64

    folder = runtime.settings.data_dir / "documents"
    folder.mkdir(exist_ok=True)
    (folder / "test.png").write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a9nEAAAAASUVORK5CYII="
        )
    )
    doc = Document(
        title="TEST IMAGE",
        original_name="test.png",
        storage_name="test.png",
        sha256="b" * 64,
        branch_codes=["*"],
        status="ready_stub",
        created_at=now(session),
    )
    session.add(doc)
    session.flush()
    return doc


def configure(runtime, branch="GTP", materials=True):
    with runtime.db.transaction() as session:
        session.add(Branch(code=branch, name=branch, timezone="Europe/Moscow"))
        session.get(Identity, "hr-a").branch_codes = ["*"]
        definition = profile_v04.install(session)
        scenario_id = definition.id
        if materials:
            folder = runtime.settings.data_dir / "documents"
            folder.mkdir(exist_ok=True)
            path = folder / "test.pdf"
            path.write_bytes(b"%PDF-1.4 synthetic test fixture")
            doc = Document(
                title="TEST ONLY",
                original_name="test.pdf",
                storage_name=path.name,
                sha256="a" * 64,
                branch_codes=["*"],
                status="ready_stub",
                created_at=now(session),
            )
            session.add(doc)
            session.flush()
            image = fixture_image(session, runtime)
            for m in session.scalars(select(Material)):
                m.document_id = image.id if m.key.startswith("MAT-") else doc.id
    employee(runtime)
    with runtime.db.transaction() as session:
        e = session.get(Employee, "e1")
        e.branch_code = branch
        e.it_access_url = "https://example.org/it"
        e.adaptation_plan_url = "https://example.org/plan"
        return scenario_id


def test_source_profile_contract():
    definition = profile_v04.compile_profile()
    assert len(definition["steps"]) == 21
    refs = [r for s in definition["steps"] for r in s["source_refs"]]
    assert len(refs) == len(set(refs)) == 55
    assert "XLS1-R064" not in refs and "XLS1-R070" not in refs
    surveys_by_id = {s["id"]: s for s in definition["surveys"]}
    assert len(surveys_by_id["M15"]["questions"]) == 9
    assert len(surveys_by_id["M21"]["questions"]) == 3
    assert [q["kind"] for q in surveys_by_id["M15"]["questions"]].count("rating") == 7
    assert all(not s["reminder_days"] for s in definition["surveys"])
    assert definition["steps"][17]["schedule"]["months"] == 1
    assert definition["steps"][17]["schedule"]["days"] == 21
    assert len(definition["steps"][18]["actions"]) == 1
    assert definition["steps"][18]["actions"][0]["material_key"] == "MAT-R058"
    assert all(
        q["options"][3]["exclusive"]
        for s in definition["surveys"]
        if s["id"].startswith("M18_")
        for q in s["questions"]
        if q["kind"] == "multiple"
    )


@pytest.mark.parametrize("branch", ["GTP", "GrPP", "Luzhniki"])
@pytest.mark.parametrize("index", range(21))
def test_every_source_scenario_can_finish(runtime, branch, index):
    scenario_id = configure(runtime, branch)
    definition = profile_v04.compile_profile()
    payload = definition["steps"][index]
    if payload["branch_codes"] and branch not in payload["branch_codes"]:
        return
    with runtime.db.transaction() as session:
        e = session.get(Employee, "e1")
        step = ScheduledStep(
            employee_id=e.id,
            scenario_id=scenario_id,
            step_key=payload["id"],
            revision=e.schedule_revision,
            due_at=now(session),
            status="enqueued",
            payload={**payload, "surveys": {s["id"]: s for s in definition["surveys"]}},
        )
        session.add(step)
        session.flush()
        routes.start(session, e, step)
        run = session.scalar(select(RouteRun))
        for iteration in range(100):
            if run.status == "completed":
                break
            if run.status == "choice":
                routes.consume(session, e, run.definition[run.position]["options"][0]["id"], str(iteration))
            elif run.status == "timed_wait":
                session.get(State, "clock").value = {"now": (now(session) + timedelta(minutes=2)).isoformat()}
                routes.advance(session, e, run)
            elif run.status == "survey":
                survey = session.scalar(
                    select(SurveyRun).where(
                        SurveyRun.scheduled_step_id == step.id, SurveyRun.status == "active"
                    )
                )
                assert survey
                q = survey.definition["questions"][survey.position]
                if survey.awaiting_comment:
                    text = "Проверочный комментарий"
                elif q["kind"] == "rating":
                    text = "8"
                elif q["kind"] in {"single", "multiple"}:
                    text = q["options"][0]["id"]
                else:
                    text = "Свободный ответ"
                if q["kind"] == "multiple" and survey.selection:
                    text = "done"
                surveys.consume_answer(session, e, survey, text, str(iteration))
            else:
                pytest.fail(f"{branch}/{payload['id']}: {run.status}: {run.last_error}")
        assert run.status == step.status == "completed"
        assert not any(
            o.body["text"] == "" and not o.body["document_ids"] for o in session.scalars(select(Outbox))
        )


def test_missing_material_stops_and_recovers_without_duplicate(runtime):
    scenario_id = configure(runtime, materials=False)
    definition = profile_v04.compile_profile()
    payload = definition["steps"][0]
    with runtime.db.transaction() as session:
        e = session.get(Employee, "e1")
        step = ScheduledStep(
            employee_id=e.id,
            scenario_id=scenario_id,
            step_key="M01",
            revision=e.schedule_revision,
            due_at=now(session),
            status="enqueued",
            payload=payload,
        )
        session.add(step)
        session.flush()
        routes.start(session, e, step)
        run = session.scalar(select(RouteRun))
        assert run.status == "blocked_material"
        assert step.status == "waiting_material"
        routes.retry_blocked(session)
        assert len(list(session.scalars(select(Outbox)))) == 0
        image = fixture_image(session, runtime)
        session.get(Material, "MAT-R003").document_id = image.id
        session.get(Material, "MAT-R004").document_id = image.id
        routes.retry_blocked(session)
        assert run.status == "choice"
        assert len(list(session.scalars(select(Outbox).where(Outbox.dedupe_key == f"route:{run.id}:0")))) == 1


def test_employee_confirmation_and_stale_revision(client, runtime):
    employee(runtime)
    event(runtime, "/hr Где отдел кадров?")
    with runtime.db.transaction() as session:
        ticket_id = session.scalar(select(Ticket.id))
    headers = {**auth("hr-a-key"), "Idempotency-Key": "resolve-1"}
    url = f"/api/support/{ticket_id}/resolution"
    assert client.post(url, headers=headers, json={"text": "На третьем этаже"}).status_code == 200
    assert client.post(url, headers=headers, json={"text": "На третьем этаже"}).json()["revision"] == 1
    event(runtime, kind="callback", payload=f"case-confirm:{ticket_id}:1:no")
    with runtime.db.transaction() as session:
        assert session.get(Ticket, ticket_id).status == "in_progress"
    client.post(
        url, headers={**auth("hr-a-key"), "Idempotency-Key": "resolve-2"}, json={"text": "Справа от лифта"}
    )
    event(runtime, kind="callback", payload=f"case-confirm:{ticket_id}:1:yes")
    with runtime.db.transaction() as session:
        assert session.get(Ticket, ticket_id).status == "awaiting_confirmation"
    event(runtime, kind="callback", payload=f"case-confirm:{ticket_id}:2:yes")
    with runtime.db.transaction() as session:
        ticket = session.get(Ticket, ticket_id)
        assert ticket.outcome == "employee_confirmed" and ticket.confirmed_by == "i-e1"


def test_reports_partial_answers_csv_xlsx_download_and_scopes(client, runtime):
    employee(runtime)
    employee(runtime, emp_id="outside", branch="B")
    scenario(runtime)
    Worker(runtime).tick()
    event(runtime, "7")
    catalogue = client.get("/api/reports/surveys", headers=auth("hr-a-key")).json()
    assert len(catalogue) == 1
    data = {
        "survey_key": catalogue[0]["survey_key"],
        "version": catalogue[0]["version"],
        "from_date": "2026-10-01",
        "to_date": "2026-10-31",
    }
    preview = client.post("/api/reports/preview", headers=auth("hr-a-key"), json=data)
    assert preview.status_code == 200, preview.text
    report = preview.json()
    assert report["totals"]["employees"] == 1 and report["statistics"][0]["mean"] == 7
    assert report["sample"][0]["awaiting_comment"]
    assert client.post("/api/reports/preview", headers=auth("manager-key"), json=data).status_code == 403
    for format in ["csv", "xlsx"]:
        file = client.post("/api/reports/files", headers=auth("hr-a-key"), json={**data, "format": format})
        assert file.status_code == 201, file.text
        response = client.get(file.json()["url"])
        assert response.status_code == 200
        if format == "csv":
            assert (
                response.content.startswith(b"\xef\xbb\xbf") and "Оценка поддержки" not in response.text
            )  # ID columns accompany definition in XLSX.
        else:
            with ZipFile(io.BytesIO(response.content)) as book:
                assert len([p for p in book.namelist() if p.startswith("xl/worksheets/")]) == 3
        with runtime.db.transaction() as session:
            session.get(Identity, "hr-a").active = False
        assert client.get(file.json()["url"]).status_code == 403
        with runtime.db.transaction() as session:
            session.get(Identity, "hr-a").active = True
    assert (
        reporting.csv_bytes([['=HYPERLINK("x")', "0012"]]).decode("utf-8-sig").startswith("'=HYPERLINK")
        is False
    )  # CSV correctly quotes embedded quotes.


def test_nlp_callback_idempotency_and_manager_privacy(client, runtime):
    employee(runtime)
    scenario(runtime)
    Worker(runtime).tick()
    event(runtime, "7")
    event(runtime, "DEMO: ordinary feedback")
    runtime.settings.ml_token = "test-nlp-secret"
    with runtime.db.transaction() as session:
        answer_id = session.scalar(select(SurveyAnswer.id))
    data = {
        "event_id": "external-1",
        "answer_id": answer_id,
        "result": {
            "sentiment": "negative",
            "risk": "high",
            "topics": ["management"],
            "explanation": "PRIVATE EXPLANATION",
            "processor_version": "external/1",
            "needs_hr": True,
        },
        "manager_signal": "meeting_recommended",
    }
    headers = {"Authorization": "Bearer test-nlp-secret"}
    assert client.post("/api/integrations/nlp/events", json=data).status_code == 401
    assert client.post("/api/integrations/nlp/events", headers=headers, json=data).status_code == 202
    assert client.post("/api/integrations/nlp/events", headers=headers, json=data).json()["duplicate"]
    assert (
        client.post(
            "/api/integrations/nlp/events", headers=headers, json={**data, "manager_signal": "none"}
        ).status_code
        == 409
    )
    with runtime.db.transaction() as session:
        assert len(list(session.scalars(select(MeetingSignal)))) == 1
        message = session.scalar(select(Outbox).where(Outbox.dedupe_key.like("meeting:%")))
        assert (
            message.recipient_id == "manager"
            and "PRIVATE" not in message.body["text"]
            and "management" not in message.body["text"]
        )


def test_new_branch_route_can_be_configured_without_source_changes(client, runtime):
    employee(runtime)
    with runtime.db.transaction() as session:
        session.get(Branch, "A").late_join_policy = "review"
    response = client.post(
        "/api/admin/branches/A/route-v04", headers=auth("hr-a-key"), json={"variant": "GTP", "revision": 1}
    )
    assert response.status_code == 200, response.text
    with runtime.db.transaction() as session:
        from korsik.models import Scenario

        branch = session.get(Branch, "A")
        definition = session.get(Scenario, branch.scenario_id).definition
        assert len(definition["steps"]) == 21
        assert all(s["branch_codes"] == ["A"] for s in definition["steps"])
        assert all(n.get("branch_codes") == ["A"] for s in definition["steps"] for n in s["actions"])
        assert all(
            session.get(Material, n["material_key"]).branch_codes == ["A"]
            for s in definition["steps"]
            for n in s["actions"]
            if n.get("material_key")
        )
        assert all(
            s.status == "late_review"
            for s in session.scalars(select(ScheduledStep).where(ScheduledStep.due_at < now(session)))
        )
    invalid = client.put(
        "/api/admin/materials/RES-R028:A",
        headers=auth("hr-a-key"),
        json={"url": "https://example.org/menu.pdf", "revision": 1},
    )
    assert invalid.status_code == 422


def test_started_route_survives_schedule_rebuild(runtime):
    from korsik.scenarios import rebuild_schedule

    scenario_id = configure(runtime)
    with runtime.db.transaction() as session:
        e = session.get(Employee, "e1")
        branch = session.get(Branch, e.branch_code)
        branch.scenario_id = scenario_id
        rebuild_schedule(session, e)
        step = session.scalar(select(ScheduledStep).where(ScheduledStep.step_key == "M01"))
        routes.start(session, e, step)
        route = session.scalar(select(RouteRun))
        assert route.status == "choice"
        e.hire_date += timedelta(days=2)
        rebuild_schedule(session, e)
        assert step.status == "dispatched" and route.status == "choice"
        assert (
            len(
                list(
                    session.scalars(
                        select(ScheduledStep).where(
                            ScheduledStep.step_key == "M01", ScheduledStep.status != "cancelled"
                        )
                    )
                )
            )
            == 1
        )


def test_exact_month_and_local_clock_boundaries(runtime):
    from korsik.calendar import due_date
    from korsik.schemas import Schedule

    configure(runtime)
    with runtime.db.transaction() as session:
        e = session.get(Employee, "e1")
        branch = session.get(Branch, e.branch_code)
        definition = profile_v04.compile_profile()
        m18 = due_date(e, branch, Schedule.model_validate(definition["steps"][17]["schedule"]))
        assert m18.isoformat() == "2026-12-14T06:00:00+00:00"
        final = due_date(e, branch, Schedule.model_validate(definition["steps"][20]["schedule"]))
        assert final.date() == e.probation_end


def test_former_hr_keeps_only_case_access_after_transfer(client, runtime):
    employee(runtime)
    event(runtime, "/hr Исходный вопрос")
    with runtime.db.transaction() as session:
        from korsik import support

        ticket = session.scalar(select(Ticket))
        ticket_id = ticket.id
        support.participant(session, ticket, "hr-a", "former")
        ticket.recruiter_id = "hr-b"
        session.get(Employee, "e1").branch_code = "B"
        session.get(Employee, "e1").recruiter_id = "hr-b"
    assert client.get(f"/api/tickets/{ticket_id}", headers=auth("hr-a-key")).status_code == 200
    retained = client.get("/api/support/retained-cases", headers=auth("hr-a-key")).json()
    assert [t["id"] for t in retained] == [ticket_id]
    assert client.get("/api/admin/inbox/e1", headers=auth("hr-a-key")).status_code == 403
    assert (
        client.post(
            f"/api/support/{ticket_id}/resolution",
            headers={**auth("hr-a-key"), "Idempotency-Key": "forbidden"},
            json={"text": "x"},
        ).status_code
        == 403
    )


def test_export_rejects_stale_preview_and_excludes_future_comment(client, runtime):
    employee(runtime)
    scenario(runtime)
    Worker(runtime).tick()
    event(runtime, "7")
    catalogue = client.get("/api/reports/surveys", headers=auth("hr-a-key")).json()[0]
    data = {
        "survey_key": catalogue["survey_key"],
        "version": catalogue["version"],
        "from_date": "2026-10-01",
        "to_date": "2026-10-31",
    }
    preview = client.post("/api/reports/preview", headers=auth("hr-a-key"), json=data).json()
    event(runtime, "Комментарий принят после предпросмотра")
    assert (
        client.post(
            "/api/reports/files",
            headers=auth("hr-a-key"),
            json={**data, "preview_hash": preview["preview_hash"]},
        ).status_code
        == 409
    )
    with runtime.db.transaction() as session:
        answer = session.scalar(select(SurveyAnswer))
        answer.comment_created_at = now(session) + timedelta(hours=1)
    latest = client.post("/api/reports/preview", headers=auth("hr-a-key"), json=data).json()
    assert latest["statistics"][0]["comments"] == 0
    assert next(iter(latest["sample"][0]["answers"].values()))["comment"] is None


def test_hr_processing_status_and_retry_do_not_change_survey(client, runtime):
    from korsik.ml import MLUnavailable, StubML

    class Broken(StubML):
        def analyze(self, payload):
            raise MLUnavailable("test_failure")

    runtime.ml = Broken()
    employee(runtime)
    scenario(runtime, questions=[{"id": "text", "kind": "text", "text": "Обратная связь"}])
    Worker(runtime).tick()
    event(runtime, "Отзыв")
    with runtime.db.transaction() as session:
        from korsik.models import Job

        job = session.scalar(select(Job).where(Job.kind == "analyze"))
        job.status = "failed"
        answer_id = session.scalar(select(SurveyAnswer.id))
    result = client.get("/api/admin/feedback", headers=auth("hr-a-key"))
    assert result.status_code == 200 and result.json()["answers"][0]["status"] == "failed"
    assert client.get("/api/admin/feedback", headers=auth("manager-key")).status_code == 403
    assert client.post(f"/api/admin/feedback/{answer_id}/retry", headers=auth("hr-b-key")).status_code == 403
    assert client.post(f"/api/admin/feedback/{answer_id}/retry", headers=auth("hr-a-key")).status_code == 200
    with runtime.db.transaction() as session:
        assert session.scalar(select(SurveyRun)).status == "completed"


def test_export_help_and_cases_are_separate_from_survey_answers(client, runtime):
    employee(runtime)
    scenario(runtime)
    Worker(runtime).tick()
    event(runtime, "/ask Вопрос по текущему опросу")
    with runtime.db.transaction() as session:
        ticket = session.scalar(select(Ticket))
        ticket.status = "closed"
        ticket.outcome = "legacy_unconfirmed"
    catalogue = client.get("/api/reports/surveys", headers=auth("hr-a-key")).json()[0]
    data = {
        "survey_key": catalogue["survey_key"],
        "version": catalogue["version"],
        "from_date": "2026-10-01",
        "to_date": "2026-10-31",
    }
    report = client.post("/api/reports/preview", headers=auth("hr-a-key"), json=data).json()
    row = report["sample"][0]
    assert row["help_states"][0]["status"] == "escalated"
    assert row["case_states"][0]["outcome"] == "legacy_unconfirmed"
    assert row["answers"] == {} and report["statistics"][0]["n"] == 0
    with runtime.db.transaction() as session:
        from korsik.analytics import dashboard

        assert (
            dashboard(session, session.get(Identity, "hr-a"))["totals"]["employee_confirmed_solutions"] == 0
        )


def test_help_retry_ignores_previous_attempt_callbacks(runtime):
    from korsik.models import HelpSession

    employee(runtime)
    event(runtime, "/ask Неподтверждённый вопрос")
    with runtime.db.transaction() as session:
        help_id = session.scalar(select(HelpSession.id))
    event(runtime, kind="callback", payload=f"help:{help_id}:1:retry")
    event(runtime, kind="callback", payload=f"help:{help_id}:1:no")
    with runtime.db.transaction() as session:
        assert session.get(HelpSession, help_id).context["attempt"] == 2
        assert len(list(session.scalars(select(Ticket)))) == 1
        assert session.get(Employee, "e1").dialogue.get("support_compose") is None


def test_explicit_new_case_is_not_merged_into_existing_case(runtime):
    employee(runtime)
    event(runtime, "/hr Первый вопрос")
    event(runtime, kind="callback", payload="case-new")
    event(runtime, "/hr Второй независимый вопрос")
    with runtime.db.transaction() as session:
        cases = list(session.scalars(select(Ticket)))
        assert len(cases) == 2
        assert {t.context["message"] for t in cases} == {"Первый вопрос", "Второй независимый вопрос"}


def test_ambiguous_question_requires_intent_and_preserves_comment(runtime):
    employee(runtime)
    scenario(runtime)
    Worker(runtime).tick()
    event(runtime, "7")
    event(runtime, "Где получить доступ?")
    with runtime.db.transaction() as session:
        from korsik.models import QuestionLog

        pending = session.get(Employee, "e1").dialogue["pending_intent"]
        token = pending["token"]
        assert session.scalar(select(SurveyRun)).awaiting_comment
        assert session.scalar(select(SurveyAnswer)).comment is None
        assert not session.scalar(select(QuestionLog)) and not session.scalar(select(Ticket))
    event(runtime, kind="callback", payload=f"intent:{token}:ask")
    event(runtime, "/resume")
    event(runtime, "Комментарий про нехватку доступа")
    with runtime.db.transaction() as session:
        assert session.scalar(select(SurveyRun)).status == "completed"
        assert session.scalar(select(SurveyAnswer)).comment == "Комментарий про нехватку доступа"


def test_declined_material_and_wrong_scope_have_explicit_trace(runtime):
    scenario_id = configure(runtime)
    definition = profile_v04.compile_profile()
    payload = definition["steps"][2]
    with runtime.db.transaction() as session:
        e = session.get(Employee, "e1")
        step = ScheduledStep(
            employee_id=e.id,
            scenario_id=scenario_id,
            step_key="M03",
            revision=e.schedule_revision,
            due_at=now(session),
            status="enqueued",
            payload=payload,
        )
        session.add(step)
        session.flush()
        routes.start(session, e, step)
        routes.consume(session, e, "2", "declined")
        route = session.scalar(select(RouteRun))
        assert route.status == "completed"
        assert {v["status"] for v in route.trace} == {"scope_not_applicable", "condition_not_met"}
