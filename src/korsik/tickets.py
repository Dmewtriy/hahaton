from sqlalchemy import select

from . import inbox
from .calendar import now
from .models import ConversationMessage, HRMessage, SurveyAnswer, SurveyRun, Ticket, TicketMessage
from .queue import send


def ticket_context(session, ticket):
    context = dict(ticket.context)
    answer = session.get(SurveyAnswer, context["answer_id"]) if context.get("answer_id") else None
    if answer:
        run = session.get(SurveyRun, answer.run_id)
        question = next(q for q in run.definition["questions"] if q["id"] == answer.question_id)
        context["question"] = question["text"]
        value = answer.value["answer"]
        options = {o["id"]: o["label"] for o in question.get("options", [])}
        context["message"] = ", ".join(
            options.get(v, str(v)) for v in (value if isinstance(value, list) else [value])
        )
        if answer.comment:
            context["message"] += "\nКомментарий: " + answer.comment
    return context


def open_ticket(session, employee, reason, context, dedupe_key, human=True):
    # `human` is retained for legacy callers; a ticket never captures the bot dialogue.
    existing = session.scalar(select(Ticket).where(Ticket.dedupe_key == dedupe_key))
    if existing:
        existing.context = {**existing.context, **context}
        if context.get("analysis"):
            detail = ticket_context(session, existing)
            inbox.append(
                session,
                employee,
                existing,
                "signal",
                signal_text(reason, detail),
                "signal-update:"
                + existing.id
                + ":"
                + context["analysis"].get("processor_version", "unknown"),
            )
        return existing
    ticket = Ticket(
        employee_id=employee.id,
        recruiter_id=context.get("recruiter_id", employee.recruiter_id),
        reason=reason,
        context=context,
        dedupe_key=dedupe_key,
        human_mode=False,
        created_at=now(session),
    )
    session.add(ticket)
    session.flush()
    from .support import participant

    participant(session, ticket, employee.recruiter_id, "original")
    participant(session, ticket, ticket.recruiter_id, "assigned")
    details = ticket_context(session, ticket)
    description = details.get("message") or details.get("question") or ""
    if context.get("initiated_by_hr"):
        inbox.append(
            session,
            employee,
            ticket,
            "system",
            reason,
            "ticket-open:" + ticket.id,
            context["initiated_by_hr"],
        )
    else:
        kind = "signal" if context.get("answer_id") or context.get("analysis") else "employee"
        body = signal_text(reason, details) if kind == "signal" else description or reason
        inbox.append(
            session,
            employee,
            ticket,
            kind,
            body,
            dedupe_key if dedupe_key.startswith("hr-in:") else "ticket-open:" + ticket.id,
            employee.identity_id if kind == "employee" else None,
        )
    from .support import case_staff_title, clip_reason

    detail = (description or "").strip()
    if detail and detail != reason:
        detail = detail[:1800]
    else:
        detail = ""
    body = f"Новое обращение\n\n{case_staff_title(ticket, employee)}\nТема: {clip_reason(reason, 120)}\n"
    if detail:
        body += f"\n{detail}\n"
    body += (
        f"\nОтветить из чата:\n"
        f"/reply {ticket.id} текст\n\n"
        f"Предложить решение:\n"
        f"/close {ticket.id} текст\n\n"
        "Или откройте кабинет — там удобнее."
    )
    notification = send(
        session,
        ticket.recruiter_id,
        body,
        f"ticket:{ticket.id}:notify",
        now(session),
    )
    notification.body = {
        **notification.body,
        "support_employee_id": employee.id,
        "support_ticket_id": ticket.id,
    }
    return ticket


def employee_message(session, employee, ticket, text, event_key):
    inbox.append(session, employee, ticket, "employee", text, "hr-in:" + event_key, employee.identity_id)
    session.add(
        TicketMessage(ticket_id=ticket.id, author_id=employee.identity_id, text=text, created_at=now(session))
    )
    from .support import case_staff_title

    notification = send(
        session,
        ticket.recruiter_id,
        f"Дополнение к обращению\n\n{case_staff_title(ticket, employee)}\n\n{employee.name}:\n{text}",
        f"ticket-message:{event_key}",
        now(session),
    )
    notification.body = {
        **notification.body,
        "support_employee_id": employee.id,
        "support_ticket_id": ticket.id,
    }


def reply_question(session, employee, ticket, *, entire_thread=False):
    query = select(HRMessage).where(HRMessage.employee_id == employee.id, HRMessage.kind == "employee")
    if not entire_thread:
        query = query.where(HRMessage.ticket_id == ticket.id)
    message = session.scalar(query.order_by(HRMessage.id.desc()).limit(1))
    if message:
        if message.dedupe_key.startswith("ticket-open:") and message.ticket_id:
            context = ticket_context(session, session.get(Ticket, message.ticket_id))
            return context.get("message") or context.get("question") or message.text
        return message.text
    context = ticket_context(session, ticket)
    return context.get("message") or context.get("question")


def reply(session, actor, employee, ticket, text, key, *, question=None):
    from .access import require
    from .support import can_write

    require(can_write(session, actor, ticket))
    require(ticket.status != "closed", "Обращение уже закрыто")
    question = question if question is not None else reply_question(session, employee, ticket)
    ticket.human_mode = False
    ticket.status = "in_progress"
    inbox.append(session, employee, ticket, "hr", text, "hr-out:" + key, actor.id)
    session.add(TicketMessage(ticket_id=ticket.id, author_id=actor.id, text=text, created_at=now(session)))
    session.add(ConversationMessage(employee_id=employee.id, role="hr", text=text, created_at=now(session)))
    from .support import case_label, open_case_numbers

    numbers, _ = open_case_numbers(session, employee.id)
    parts = [case_label(ticket, numbers.get(ticket.id)), ""]
    if question:
        parts += [f"Ваш вопрос:\n{question}", ""]
    parts += [f"Ответ HR:\n{text}"]
    send(
        session,
        employee.identity_id,
        "\n".join(parts),
        f"hr-reply:{key}",
        now(session),
        buttons=[{"text": "Ответить HR", "payload": "case-reply:" + ticket.id}],
    )


def close(session, actor, employee, ticket, key):
    from fastapi import HTTPException

    from .support import resolution

    if ticket.status in {"closed", "awaiting_confirmation"}:
        return
    if not ticket.resolution:
        raise HTTPException(
            409, "Сначала опишите решение. Обращение закрывает сотрудник своим подтверждением"
        )
    resolution(session, actor, ticket, ticket.resolution, key)


def signal_text(reason, context):
    parts = [
        reason,
        context.get("question"),
        context.get("message"),
        context.get("analysis", {}).get("explanation"),
    ]
    return "\n".join(str(part) for part in parts if part)
