"""Explicit case ownership, recipient choice and employee-confirmed resolutions."""

from fastapi import HTTPException
from sqlalchemy import select

from .access import can_support, is_hr, require
from .calendar import now
from .models import Branch, Employee, HRMessage, Identity, SurveyAnswer, SurveyRun, Ticket, TicketParticipant
from .queue import send


def allowed_recipients(session, employee):
    branch = session.get(Branch, employee.branch_code)
    ids = [employee.recruiter_id, *branch.support_recipient_ids]
    return [
        actor
        for identity_id in dict.fromkeys(ids)
        if (actor := session.get(Identity, identity_id))
        and actor.active
        and is_hr(actor)
        and can_support(actor, employee)
    ]


def can_read(session, actor, ticket):
    employee = session.get(Employee, ticket.employee_id)
    grant = session.get(TicketParticipant, (ticket.id, actor.id))
    return bool(
        actor.active
        and (
            employee.identity_id == actor.id
            or can_support(actor, employee)
            or is_hr(actor)
            and grant
            and grant.kind in {"former", "original"}
        )
    )


def can_write(session, actor, ticket):
    employee = session.get(Employee, ticket.employee_id)
    return bool(actor.active and is_hr(actor) and can_support(actor, employee))


def participant(session, ticket, identity_id, kind):
    value = session.get(TicketParticipant, (ticket.id, identity_id))
    if not value:
        session.add(
            TicketParticipant(
                ticket_id=ticket.id, identity_id=identity_id, kind=kind, created_at=now(session)
            )
        )
    else:
        value.kind = kind


def choose_recipient(session, employee, key, *, text=None, context=None, ticket_id=None):
    recipients = allowed_recipients(session, employee)
    employee.dialogue = {
        **employee.dialogue,
        "support_compose": {"text": text, "context": context or {}, "ticket_id": ticket_id},
    }
    if len(recipients) == 1:
        select_recipient(session, employee, recipients[0].id, key)
        return
    if not recipients:
        send(
            session,
            employee.identity_id,
            "Нет действующего получателя HR. Требуется исправить назначение сотрудника.",
            "support-no-owner:" + key,
            now(session),
        )
        return
    buttons = [
        {
            "text": actor.name + (" (закреплённый HR)" if actor.id == employee.recruiter_id else ""),
            "payload": "support-to:" + actor.id,
        }
        for actor in recipients
    ]
    send(
        session,
        employee.identity_id,
        "Выберите ответственного. Закреплённый и прежний HR сохраняют доступ к этому обращению; оно не является анонимным.",
        "support-recipients:" + key,
        now(session),
        buttons=buttons,
    )


def select_recipient(session, employee, identity_id, key):
    require(identity_id in {i.id for i in allowed_recipients(session, employee)}, "Получатель недоступен")
    pending = employee.dialogue.get("support_compose")
    if not pending:
        send(
            session,
            employee.identity_id,
            "Выбор устарел. Начните с /hr.",
            "support-stale:" + key,
            now(session),
        )
        return
    pending = {**pending, "recipient_id": identity_id}
    employee.dialogue = {**employee.dialogue, "support_compose": pending}
    if pending.get("text"):
        submit(session, employee, pending["text"], key)
    else:
        send(
            session,
            employee.identity_id,
            "Напишите одно сообщение для выбранного HR. Опрос сохранён; отмена: /cancel.",
            "support-text:" + key,
            now(session),
        )


def submit(session, employee, text, key, *, context=None):
    from . import tickets

    pending = employee.dialogue.get("support_compose", {})
    identity_id = pending.get("recipient_id", employee.recruiter_id)
    if identity_id not in {i.id for i in allowed_recipients(session, employee)}:
        choose_recipient(
            session,
            employee,
            key,
            text=text,
            context=pending.get("context"),
            ticket_id=pending.get("ticket_id"),
        )
        return None
    ticket = session.get(Ticket, pending.get("ticket_id")) if pending.get("ticket_id") else None
    if ticket:
        require(ticket.employee_id == employee.id and ticket.status != "closed")
        old = ticket.recruiter_id
        participant(session, ticket, old, "former")
        ticket.recruiter_id = identity_id
        participant(session, ticket, identity_id, "assigned")
        tickets.employee_message(session, employee, ticket, text, key)
    else:
        ctx = {**pending.get("context", {}), **(context or {}), "message": text, "recruiter_id": identity_id}
        ticket = tickets.open_ticket(session, employee, "Сообщение сотрудника", ctx, "hr-in:" + key)
        participant(session, ticket, employee.recruiter_id, "original")
        participant(session, ticket, identity_id, "assigned")
    from .models import HelpSession

    help_id = pending.get("context", {}).get("help_id")
    help_session = session.get(HelpSession, help_id) if help_id else None
    if help_session and help_session.employee_id == employee.id:
        help_session.ticket_id = ticket.id
        help_session.status = "escalated"
    employee.dialogue = {
        k: v
        for k, v in employee.dialogue.items()
        if k not in {"support_compose", "awaiting_hr_message", "awaiting_ai_question"}
    }
    actor = session.get(Identity, ticket.recruiter_id)
    send(
        session,
        employee.identity_id,
        f"Обращение №{ticket.id} зарегистрировано. Ответственный: {actor.name}. Закреплённый и прежний HR сохраняют доступ к этому обращению; оно не анонимное. Доставка и прочтение HR учитываются отдельно. Опросы продолжают работать.",
        "support-registered:" + key,
        now(session),
        buttons=[
            {"text": "Дополнить это обращение", "payload": "case-reply:" + ticket.id},
            {"text": "Другой ответственный", "payload": "case-owner:" + ticket.id},
        ],
    )
    return ticket


def context_for_question(session, employee, run_id, question_id):
    run = session.get(SurveyRun, run_id)
    require(bool(run and run.employee_id == employee.id))
    question = next((q for q in run.definition["questions"] if q["id"] == question_id), None)
    require(bool(question))
    answer = session.scalar(
        select(SurveyAnswer).where(SurveyAnswer.run_id == run.id, SurveyAnswer.question_id == question_id)
    )
    require(bool(answer), "Сначала сохраните ответ")
    return {
        "run_id": run.id,
        "question_id": question_id,
        "question": question["text"],
        "answer_id": answer.id,
        "source_ref": question.get("source_ref"),
        "answer": answer.value,
        "comment": answer.comment,
    }


def resolution(session, actor, ticket, text, key):
    from . import inbox

    require(can_write(session, actor, ticket))
    require(ticket.status != "closed", "Обращение закрыто")
    duplicate = session.scalar(select(HRMessage).where(HRMessage.dedupe_key == "resolution:" + key))
    if duplicate:
        if duplicate.text != "Предложенное решение: " + text:
            raise HTTPException(409, "Ключ решения использован с другим текстом")
        return
    ticket.resolution = text
    ticket.resolution_revision += 1
    ticket.status = "awaiting_confirmation"
    ticket.confirmed_at = None
    ticket.confirmed_by = None
    ticket.outcome = None
    employee = session.get(Employee, ticket.employee_id)
    inbox.append(
        session, employee, ticket, "hr", "Предложенное решение: " + text, "resolution:" + key, actor.id
    )
    from .tickets import reply_question

    question = reply_question(session, employee, ticket)
    send(
        session,
        employee.identity_id,
        f"Обращение №{ticket.id}\n"
        + (f"Ваш вопрос: {question}\n" if question else "")
        + f"Ответ HR: {text}\nВопрос решён?",
        f"resolution:{ticket.id}:{ticket.resolution_revision}",
        now(session),
        buttons=[
            {
                "text": "Да, вопрос решён",
                "payload": f"case-confirm:{ticket.id}:{ticket.resolution_revision}:yes",
            },
            {
                "text": "Нет, проблема осталась",
                "payload": f"case-confirm:{ticket.id}:{ticket.resolution_revision}:no",
            },
        ],
    )


def confirm(session, employee, ticket_id, revision, value, key):
    from . import inbox

    ticket = session.get(Ticket, ticket_id)
    require(bool(ticket and ticket.employee_id == employee.id))
    if ticket.status != "awaiting_confirmation" or ticket.resolution_revision != revision:
        send(
            session,
            employee.identity_id,
            "Это подтверждение устарело. Используйте последнее решение обращения.",
            "case-stale:" + key,
            now(session),
        )
        return
    if value == "yes":
        ticket.status = "closed"
        ticket.confirmed_by = employee.identity_id
        ticket.confirmed_at = now(session)
        ticket.closed_at = now(session)
        ticket.outcome = "employee_confirmed"
    elif value == "no":
        ticket.status = "in_progress"
        ticket.outcome = "needs_revision"
    else:
        raise HTTPException(422, "Неизвестное действие")
    inbox.append(
        session,
        employee,
        ticket,
        "system",
        "Сотрудник подтвердил решение." if value == "yes" else "Сотрудник сообщил: проблема осталась.",
        "case-confirm:" + key,
        employee.identity_id,
    )
    send(
        session,
        employee.identity_id,
        "Обращение закрыто по вашему подтверждению."
        if value == "yes"
        else "Обращение возвращено HR в работу под тем же номером.",
        "case-confirm-ack:" + key,
        now(session),
    )
    send(
        session,
        ticket.recruiter_id,
        f"Обращение №{ticket.id}: "
        + (
            "сотрудник подтвердил решение."
            if value == "yes"
            else "проблема осталась, требуется доработать решение."
        ),
        "case-confirm-notify:" + key,
        now(session),
    )


def callback(session, employee, payload, key):
    if payload == "case-new" or payload.startswith("case-target:"):
        pending = employee.dialogue.get("support_compose", {})
        ticket_id = payload.split(":", 1)[1] if payload.startswith("case-target:") else None
        if ticket_id:
            ticket = session.get(Ticket, ticket_id)
            require(bool(ticket and ticket.employee_id == employee.id and ticket.status != "closed"))
            owner = ticket.recruiter_id
        else:
            owner = employee.recruiter_id
        employee.dialogue = {
            **employee.dialogue,
            "support_compose": {
                **pending,
                "ticket_id": ticket_id,
                "recipient_id": owner,
                "force_new": payload == "case-new",
            },
        }
        if pending.get("text"):
            submit(session, employee, pending["text"], key)
        else:
            send(
                session,
                employee.identity_id,
                "Напишите сообщение для обращения. Отмена: /cancel.",
                "case-text:" + key,
                now(session),
            )
        return True
    if payload.startswith("support-to:"):
        select_recipient(session, employee, payload.split(":", 1)[1], key)
        return True
    if payload.startswith(("support:", "contact:")):
        _, run_id, question_id = payload.split(":", 2)
        context = context_for_question(session, employee, run_id, question_id)
        run = session.get(SurveyRun, run_id)
        question = next(q for q in run.definition["questions"] if q["id"] == question_id)
        if (
            run.status == "active"
            and run.awaiting_comment
            and run.definition["questions"][run.position]["id"] == question_id
            and question.get("comment_contact_alternative")
        ):
            answer = session.get(SurveyAnswer, context["answer_id"])
            answer.value = {
                **answer.value,
                "comment_channel": "personal_hr" if payload.startswith("contact:") else "bot_hr",
            }
            run.awaiting_comment = False
            from .surveys import advance

            advance(session, employee, run, key)
        if payload.startswith("contact:"):
            hr = session.get(Identity, employee.recruiter_id)
            if hr and hr.active and hr.contact_url:
                send(
                    session,
                    employee.identity_id,
                    "Личный контакт рекрутера. Передайте ему причину выбранного ответа.",
                    "personal-contact:" + key,
                    now(session),
                    buttons=[{"type": "link", "text": hr.name, "url": hr.contact_url}],
                )
                return True
        choose_recipient(session, employee, key, context=context)
        return True
    if payload.startswith(("case-reply:", "case-owner:")):
        action, ticket_id = payload.split(":", 1)
        ticket = session.get(Ticket, ticket_id)
        require(bool(ticket and ticket.employee_id == employee.id and ticket.status != "closed"))
        if action == "case-owner":
            choose_recipient(session, employee, key, ticket_id=ticket.id)
        else:
            employee.dialogue = {
                **employee.dialogue,
                "support_compose": {"ticket_id": ticket.id, "recipient_id": ticket.recruiter_id},
            }
            send(
                session,
                employee.identity_id,
                f"Напишите дополнение к обращению №{ticket.id}. Отмена: /cancel.",
                "case-compose:" + key,
                now(session),
            )
        return True
    if payload.startswith("case-confirm:"):
        _, ticket_id, revision, value = payload.split(":", 3)
        confirm(session, employee, ticket_id, int(revision), value, key)
        return True
    return False


def list_cases(session, employee, key, text=None):
    rows = list(
        session.scalars(
            select(Ticket)
            .where(Ticket.employee_id == employee.id, Ticket.status != "closed")
            .order_by(Ticket.created_at.desc())
        )
    )
    employee.dialogue = {**employee.dialogue, "support_compose": {"text": text}}
    labels = {
        "open": "Новое",
        "in_progress": "В работе",
        "awaiting_confirmation": "Ожидает вашего подтверждения",
    }
    send(
        session,
        employee.identity_id,
        "Выберите обращение:\n"
        + "\n".join(f"№{t.id}: {t.reason} — {labels.get(t.status, t.status)}" for t in rows),
        "case-list:" + key,
        now(session),
        buttons=[
            {
                "text": "№" + t.id[:8] + " · " + labels.get(t.status, t.status),
                "payload": "case-target:" + t.id,
            }
            for t in rows[:15]
        ]
        + [{"text": "Новое обращение", "payload": "case-new"}],
    )
