from sqlalchemy import select

from .calendar import now
from .models import ConversationMessage, Ticket, TicketMessage
from .queue import send


def open_ticket(session, employee, reason, context, dedupe_key, human=True):
    existing = session.scalar(select(Ticket).where(Ticket.dedupe_key == dedupe_key))
    if existing:
        existing.context = {**existing.context, **context}
        return existing
    # A human conversation is one inbox thread, not one ticket per incoming sentence.
    if human:
        existing = active_human_ticket(session, employee)
        if existing:
            return existing
    ticket = Ticket(
        employee_id=employee.id,
        recruiter_id=employee.recruiter_id,
        reason=reason,
        context=context,
        dedupe_key=dedupe_key,
        human_mode=human,
        created_at=now(session),
    )
    session.add(ticket)
    session.flush()
    send(
        session,
        employee.recruiter_id,
        f"Новое обращение {ticket.id}\nСотрудник: {employee.name}\nПричина: {reason}\n"
        f"Ответ: /reply {ticket.id} текст ответа\nЗакрытие: /close {ticket.id}",
        f"ticket:{ticket.id}:notify",
        now(session),
    )
    return ticket


def active_human_ticket(session, employee):
    return session.scalar(
        select(Ticket)
        .where(Ticket.employee_id == employee.id, Ticket.status != "closed", Ticket.human_mode.is_(True))
        .order_by(Ticket.created_at)
    )


def employee_message(session, employee, ticket, text, event_key):
    session.add(
        TicketMessage(ticket_id=ticket.id, author_id=employee.identity_id, text=text, created_at=now(session))
    )
    send(
        session,
        ticket.recruiter_id,
        f"Обращение {ticket.id}\n{employee.name}: {text}",
        f"ticket-message:{event_key}",
        now(session),
    )
    send(
        session,
        employee.identity_id,
        "Сообщение передано рекрутеру.",
        f"ticket-ack:{event_key}",
        now(session),
    )


def reply(session, actor, employee, ticket, text, key):
    from .access import can_support, require

    require(can_support(actor, employee) and ticket.recruiter_id == actor.id)
    require(ticket.status != "closed", "Обращение уже закрыто")
    active = active_human_ticket(session, employee)
    if active and active.id != ticket.id:
        from fastapi import HTTPException

        raise HTTPException(409, "Сначала завершите уже открытый человеческий диалог")
    ticket.human_mode = True
    ticket.status = "in_progress"
    session.add(TicketMessage(ticket_id=ticket.id, author_id=actor.id, text=text, created_at=now(session)))
    session.add(ConversationMessage(employee_id=employee.id, role="hr", text=text, created_at=now(session)))
    send(session, employee.identity_id, f"Ответ HR ({actor.name}):\n{text}", f"hr-reply:{key}", now(session))


def close(session, actor, employee, ticket, key):
    from .access import can_support, require
    from .scenarios import drain_dialogue_queue
    from .surveys import active_run, prompt

    require(can_support(actor, employee) and ticket.recruiter_id == actor.id)
    if ticket.status == "closed":
        return
    ticket.status = "closed"
    ticket.human_mode = False
    ticket.closed_at = now(session)
    send(
        session, employee.identity_id, "Обращение закрыто. Продолжим адаптацию.", f"close:{key}", now(session)
    )
    session.flush()
    run = active_run(session, employee)
    if run:
        prompt(session, employee, run, key)
    else:
        drain_dialogue_queue(session, employee)
