from datetime import date

from conftest import auth, clock, employee, event, scenario
from sqlalchemy import select

from korsik.calendar import add_months, due_date
from korsik.models import Branch, Outbox, ScheduledStep, SurveyAnswer, SurveyRun
from korsik.schemas import Schedule
from korsik.worker import Worker


def test_calendar_weekends_holidays_and_timezone(runtime):
    emp = employee(runtime)
    with runtime.db.transaction() as session:
        branch = session.get(Branch, "A")
        branch.holidays = ["2026-10-26"]
        assert (
            due_date(emp, branch, Schedule(working_day=3, at="09:00")).isoformat()
            == "2026-10-28T06:00:00+00:00"
        )
        emp.timezone = "Asia/Novosibirsk"
        assert due_date(emp, branch, Schedule(at="09:00")).hour == 2
        branch.working_dates = ["2026-10-24"]
        assert due_date(emp, branch, Schedule(working_day=2)).date() == date(2026, 10, 24)
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)


def test_duplicate_event_and_worker_restart_do_not_repeat_answer(runtime):
    employee(runtime)
    scenario(runtime)
    Worker(runtime).tick()
    event(runtime, "8", event_id="same")
    event(runtime, "8", event_id="same")
    Worker(runtime).tick()
    with runtime.db.transaction() as session:
        assert len(list(session.scalars(select(SurveyAnswer)))) == 1
        assert session.scalar(select(SurveyRun)).status == "completed"


def test_rating_below_8_requests_comment_and_8_does_not(runtime):
    employee(runtime)
    scenario(runtime)
    Worker(runtime).tick()
    event(runtime, "7")
    with runtime.db.transaction() as session:
        run = session.scalar(select(SurveyRun))
        assert run.awaiting_comment and run.position == 0
    event(runtime, "Нужно больше помощи")
    with runtime.db.transaction() as session:
        assert session.scalar(select(SurveyAnswer)).comment == "Нужно больше помощи"
        assert session.scalar(select(SurveyRun)).status == "completed"


def test_invalid_rating_does_not_advance(runtime):
    employee(runtime)
    scenario(runtime)
    Worker(runtime).tick()
    for answer in ["hello", "11", "0", "7.5"]:
        event(runtime, answer)
    with runtime.db.transaction() as session:
        assert session.scalar(select(SurveyRun)).position == 0
        assert not list(session.scalars(select(SurveyAnswer)))


def test_multi_select_and_stale_callback(runtime):
    employee(runtime)
    scenario(
        runtime,
        questions=[
            {
                "id": "helpers",
                "text": "Кто помогает?",
                "kind": "multiple",
                "options": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
            }
        ],
    )
    Worker(runtime).tick()
    with runtime.db.transaction() as session:
        run_id = session.scalar(select(SurveyRun.id))
    event(runtime, kind="callback", payload=f"a:{run_id}:1:a")
    event(runtime, kind="callback", payload=f"a:{run_id}:0:a")
    event(runtime, kind="callback", payload=f"a:{run_id}:0:b")
    event(runtime, "/done")
    with runtime.db.transaction() as session:
        assert session.scalar(select(SurveyAnswer)).value["answer"] == ["a", "b"]


def test_ai_detour_preserves_comment_and_question_position(runtime):
    from korsik.ml import AnswerResult, StubML

    class ClarifyingML(StubML):
        def answer(self, payload):
            return AnswerResult(status="clarification", answer="Уточните систему", processor_version="test/1")

    runtime.ml = ClarifyingML()
    employee(runtime)
    scenario(runtime)
    Worker(runtime).tick()
    event(runtime, "5")
    event(runtime, "/ask Как получить доступ?")
    with runtime.db.transaction() as session:
        run = session.scalar(select(SurveyRun))
        assert run.awaiting_comment and run.position == 0
    event(runtime, "Нужен доступ к системе")
    with runtime.db.transaction() as session:
        assert session.scalar(select(SurveyRun)).awaiting_comment
    event(runtime, "/resume")
    event(runtime, "Причина оценки относится к опросу")
    with runtime.db.transaction() as session:
        assert session.scalar(select(SurveyRun)).status == "completed"


def test_busy_survey_queues_steps_and_returns_after_completion(runtime):
    employee(runtime)
    scenario(
        runtime,
        steps=[
            {"id": "survey", "text": "Опрос", "kind": "survey", "survey_id": "s"},
            {"id": "next", "text": "Материал после опроса", "schedule": {"at": "09:01"}},
        ],
    )
    Worker(runtime).tick()
    clock(runtime, "2026-10-23T06:02:00+00:00")
    Worker(runtime).tick()
    with runtime.db.transaction() as session:
        step = session.scalar(select(ScheduledStep).where(ScheduledStep.step_key == "next"))
        assert step.status == "queued"
    event(runtime, "8")
    with runtime.db.transaction() as session:
        assert (
            session.scalar(select(ScheduledStep).where(ScheduledStep.step_key == "next")).status
            == "dispatched"
        )


def test_reminders_only_for_unfinished_surveys(runtime):
    employee(runtime)
    scenario(runtime)
    Worker(runtime).tick()
    clock(runtime, "2026-10-24T06:00:01+00:00")
    Worker(runtime).tick()
    with runtime.db.transaction() as session:
        reminders = list(session.scalars(select(Outbox).where(Outbox.dedupe_key.like("reminder:%"))))
        assert len(reminders) == 1
    event(runtime, "8")
    clock(runtime, "2026-10-25T06:00:01+00:00")
    Worker(runtime).tick()
    with runtime.db.transaction() as session:
        assert len(list(session.scalars(select(Outbox).where(Outbox.dedupe_key.like("reminder:%"))))) == 1


def test_reschedule_cancels_future_and_retains_completed_steps(client, runtime):
    employee(runtime)
    scenario(
        runtime,
        steps=[{"id": "now", "text": "Now"}, {"id": "later", "text": "Later", "schedule": {"days": 10}}],
    )
    Worker(runtime).tick()
    result = client.patch(
        "/api/employees/e1/dates", headers=auth("hr-a-key"), json={"hire_date": "2026-10-26"}
    )
    assert result.status_code == 200
    with runtime.db.transaction() as session:
        rows = list(session.scalars(select(ScheduledStep)))
        assert sum(s.step_key == "now" for s in rows) == 1
        assert sum(s.status == "cancelled" for s in rows) == 1
        future = next(s for s in rows if s.step_key == "later" and s.status == "pending")
        assert future.due_at.date() == date(2026, 11, 5)


def test_manual_step_only_runs_after_hr_trigger(client, runtime):
    employee(runtime)
    scenario(runtime, steps=[{"id": "manual", "text": "Manual", "manual": True}])
    Worker(runtime).tick()
    with runtime.db.transaction() as session:
        step = session.scalar(select(ScheduledStep))
        assert step.status == "manual"
        step_id = step.id
    assert client.post(f"/api/steps/{step_id}/trigger", headers=auth("manager-key")).status_code == 403
    assert client.post(f"/api/steps/{step_id}/trigger", headers=auth("hr-a-key")).status_code == 200
    Worker(runtime).tick()
    assert client.post(f"/api/steps/{step_id}/trigger", headers=auth("hr-a-key")).status_code == 409


def test_branch_and_category_filter(runtime):
    employee(runtime)
    scenario(
        runtime,
        steps=[
            {"id": "wrong_branch", "text": "No", "branch_codes": ["B"]},
            {"id": "wrong_category", "text": "No", "categories": ["production"]},
            {"id": "yes", "text": "Yes"},
        ],
    )
    with runtime.db.transaction() as session:
        assert [s.step_key for s in session.scalars(select(ScheduledStep))] == ["yes"]


def test_source_scenario_is_valid():
    from korsik.seed import definition

    data = definition()
    assert len(data["steps"]) >= 25
    assert all(s["schedule"]["at"] for s in data["steps"])
