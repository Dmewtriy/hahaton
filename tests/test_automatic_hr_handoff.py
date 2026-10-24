from conftest import auth, employee, event, scenario
from sqlalchemy import select

from korsik.ml import MLUnavailable, StubML
from korsik.models import Outbox, SurveyRun, Ticket, TicketMessage
from korsik.worker import Worker


def test_unknown_answer_automatically_hands_over_and_resumes_survey(client, runtime):
    employee(runtime)
    scenario(runtime)
    Worker(runtime).tick()
    event(runtime, "/ask Неизвестный вопрос")
    with runtime.db.transaction() as session:
        ticket = session.scalar(select(Ticket))
        assert ticket and not ticket.human_mode and ticket.recruiter_id == "hr-a"
        assert ticket.context["question"] == "Неизвестный вопрос"
        ticket_id = ticket.id
        assert session.scalar(select(SurveyRun)).position == 0
        messages = list(session.scalars(select(Outbox)))
        assert any(o.recipient_id == "hr-a" and "Неизвестный вопрос" in o.body["text"] for o in messages)
        assert any("не нашёл подтверждённого ответа" in o.body["text"] for o in messages)
    event(runtime, "/hr Дополнительный контекст")
    with runtime.db.transaction() as session:
        assert len(list(session.scalars(select(TicketMessage)))) == 1
        assert len(list(session.scalars(select(Ticket)))) == 1
    assert client.post(f"/api/tickets/{ticket_id}/close", headers=auth("hr-a-key")).status_code == 200
    event(runtime, "8")
    with runtime.db.transaction() as session:
        assert session.scalar(select(SurveyRun)).status == "completed"


def test_blocked_source_and_ml_failure_also_transfer_to_hr(runtime):
    class Broken(StubML):
        def answer(self, payload):
            raise MLUnavailable("timeout")

    employee(runtime)
    runtime.ml = Broken()
    event(runtime, "/ask Вопрос")
    with runtime.db.transaction() as session:
        ticket = session.scalar(select(Ticket))
        assert not ticket.human_mode and ticket.context["answer_status"] == "unavailable"
        assert any("Передал ваш вопрос" in o.body["text"] for o in session.scalars(select(Outbox)))
