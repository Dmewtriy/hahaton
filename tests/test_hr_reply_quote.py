from conftest import auth, employee, event
from sqlalchemy import select

from korsik import tickets
from korsik.models import Identity, Outbox, Ticket


def test_inbox_hr_answer_quotes_latest_employee_message(client, runtime):
    employee(runtime)
    employee(runtime, emp_id="other")
    event(runtime, "/hr Где отдел кадров?")
    event(runtime, "/hr Где именно на третьем этаже?")
    event(runtime, "/hr Чужой вопрос", emp_id="other")
    response = client.post(
        "/api/admin/inbox/e1/messages",
        headers={**auth("hr-a-key"), "Idempotency-Key": "quote-latest"},
        json={"text": "Справа от лифта"},
    )
    assert response.status_code == 201, response.text
    with runtime.db.transaction() as session:
        outgoing = session.scalar(select(Outbox).where(Outbox.dedupe_key.like("hr-reply:%")))
        assert outgoing.body["text"] == (
            "Ваш вопрос: Где именно на третьем этаже?\nОтвет HR: Справа от лифта"
        )
        assert outgoing.body["buttons"][0]["text"] == "Ответить HR"


def test_ticket_specific_reply_quotes_its_question_instead_of_another_case(client, runtime):
    person = employee(runtime)
    with runtime.db.transaction() as session:
        first = tickets.open_ticket(session, person, "Первый", {"question": "Где отдел кадров?"}, "q1")
        first_id = first.id
        tickets.open_ticket(session, person, "Второй", {"question": "Где столовая?"}, "q2")
    response = client.post(
        f"/api/tickets/{first_id}/reply",
        headers={**auth("hr-a-key"), "Idempotency-Key": "specific"},
        json={"text": "На третьем этаже"},
    )
    assert response.status_code == 200
    with runtime.db.transaction() as session:
        outgoing = session.scalar(select(Outbox).where(Outbox.dedupe_key.like("hr-reply:%")))
        assert outgoing.body["text"] == "Ваш вопрос: Где отдел кадров?\nОтвет HR: На третьем этаже"


def test_hr_initiated_message_does_not_invent_employee_question(runtime):
    person = employee(runtime)
    with runtime.db.transaction() as session:
        actor = session.get(Identity, "hr-a")
        ticket = tickets.open_ticket(session, person, "Помощь", {"initiated_by_hr": actor.id}, "hr-start")
        tickets.reply(session, actor, person, ticket, "Как проходит адаптация?", "hr-init")
    with runtime.db.transaction() as session:
        outgoing = session.scalar(select(Outbox).where(Outbox.dedupe_key == "hr-reply:hr-init"))
        assert outgoing.body["text"] == "Ответ HR: Как проходит адаптация?"
        assert session.scalar(select(Ticket)).human_mode is False
