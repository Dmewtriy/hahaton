"""Employee-centred HR history and per-reader cursors; no global human-dialogue lock."""

from fastapi import HTTPException
from sqlalchemy import and_, func, select

from . import access, analytics
from .calendar import now
from .models import Branch, Employee, HRMessage, HRReadState, Identity, Ticket


def append(session, employee, ticket, kind, text, key, author_id=None):
    session.scalar(select(Employee.id).where(Employee.id == employee.id).with_for_update())
    existing = session.scalar(select(HRMessage).where(HRMessage.dedupe_key == key))
    if existing:
        return existing
    message = HRMessage(
        employee_id=employee.id,
        ticket_id=ticket.id if ticket else None,
        author_id=author_id,
        kind=kind,
        text=text,
        dedupe_key=key,
        created_at=now(session),
    )
    session.add(message)
    session.flush()
    return message


def employee_thread(session, actor, employee_id):
    employee = session.scalar(select(Employee).where(Employee.id == employee_id).with_for_update())
    if not employee:
        raise HTTPException(404, "Сотрудник не найден")
    access.require(access.can_support(actor, employee))
    return employee


def threads(session, actor, scope="mine", archived=False):
    access.require(access.is_hr(actor))
    people = [
        e
        for e in analytics.visible_employees(session, actor)
        if e.active != archived and (scope == "all" or e.recruiter_id == actor.id)
    ]
    ids = [e.id for e in people]
    if not ids:
        return []
    last_ids = (
        select(HRMessage.employee_id, func.max(HRMessage.id).label("last_id"))
        .where(HRMessage.employee_id.in_(ids))
        .group_by(HRMessage.employee_id)
        .subquery()
    )
    latest = {
        m.employee_id: m
        for m in session.scalars(select(HRMessage).join(last_ids, HRMessage.id == last_ids.c.last_id))
    }
    unread = dict(
        session.execute(
            select(HRMessage.employee_id, func.count(HRMessage.id))
            .outerjoin(
                HRReadState,
                and_(HRReadState.employee_id == HRMessage.employee_id, HRReadState.reader_id == actor.id),
            )
            .where(
                HRMessage.employee_id.in_(ids),
                HRMessage.kind.in_(["employee", "signal"]),
                HRMessage.id > func.coalesce(HRReadState.last_read_id, 0),
            )
            .group_by(HRMessage.employee_id)
        ).all()
    )
    opened = dict(
        session.execute(
            select(Ticket.employee_id, func.count(Ticket.id))
            .where(Ticket.employee_id.in_(ids), Ticket.status != "closed")
            .group_by(Ticket.employee_id)
        ).all()
    )
    branches = {b.code: b.name for b in session.scalars(select(Branch))}
    staff = {i.id: i.name for i in session.scalars(select(Identity))}
    result = []
    for employee in people:
        last = latest.get(employee.id)
        result.append(
            {
                "employee_id": employee.id,
                "name": employee.name,
                "department": employee.department,
                "branch_code": employee.branch_code,
                "branch_name": branches[employee.branch_code],
                "recruiter_id": employee.recruiter_id,
                "recruiter_name": staff[employee.recruiter_id],
                "unread_count": unread.get(employee.id, 0),
                "open_tickets": opened.get(employee.id, 0),
                "last_message": last.text[:180] if last else "",
                "last_message_id": last.id if last else 0,
                "last_at": last.created_at if last else None,
                "active": employee.active,
            }
        )
    return sorted(result, key=lambda row: (row["unread_count"] > 0, row["last_message_id"]), reverse=True)


def history(session, actor, employee_id, *, before=0, after=0, limit=100):
    employee = employee_thread(session, actor, employee_id)
    query = select(HRMessage).where(HRMessage.employee_id == employee_id)
    if before:
        query = query.where(HRMessage.id < before)
    if after:
        query = query.where(HRMessage.id > after)
    messages = list(
        session.scalars(query.order_by(HRMessage.id.asc() if after else HRMessage.id.desc()).limit(limit + 1))
    )
    more = len(messages) > limit
    messages = messages[:limit]
    if not after:
        messages.reverse()
    authors = {i.id: i.name for i in session.scalars(select(Identity))}
    return {
        "employee_id": employee.id,
        "name": employee.name,
        "department": employee.department,
        "recruiter_id": employee.recruiter_id,
        "active": employee.active,
        "has_more": more,
        "messages": [
            {
                "id": m.id,
                "ticket_id": m.ticket_id,
                "kind": m.kind,
                "text": m.text,
                "author_name": authors.get(m.author_id, "Система"),
                "at": m.created_at,
            }
            for m in messages
        ],
    }


def mark_read(session, actor, employee_id, last_message_id):
    employee_thread(session, actor, employee_id)
    message = session.get(HRMessage, last_message_id)
    if not message or message.employee_id != employee_id:
        raise HTTPException(422, "Сообщение не принадлежит этой переписке")
    # The employee row is locked by the endpoint; a concurrent incoming message
    # gets a later ID and cannot be accidentally marked read by this snapshot.
    state = session.get(HRReadState, (employee_id, actor.id))
    if not state:
        session.add(HRReadState(employee_id=employee_id, reader_id=actor.id, last_read_id=last_message_id))
    else:
        state.last_read_id = max(state.last_read_id, last_message_id)


def send_employee_message(session, employee, text, key, reason="Сообщение сотрудника"):
    from . import tickets

    existing = session.scalar(select(HRMessage).where(HRMessage.dedupe_key == "hr-in:" + key))
    if existing:
        return session.get(Ticket, existing.ticket_id)
    ticket = session.scalar(
        select(Ticket)
        .where(Ticket.employee_id == employee.id, Ticket.status != "closed")
        .order_by(Ticket.created_at.desc(), Ticket.id.desc())
    )
    if not ticket:
        ticket = tickets.open_ticket(session, employee, reason, {"message": text}, "hr-in:" + key)
    else:
        ticket.recruiter_id = employee.recruiter_id
        tickets.employee_message(session, employee, ticket, text, key)
    return ticket


def reply(session, actor, employee, text, key):
    from . import tickets

    existing = session.scalar(select(HRMessage).where(HRMessage.dedupe_key == "hr-out:" + key))
    if existing:
        if existing.text != text:
            raise HTTPException(409, "Ключ отправки уже использован для другого сообщения")
        return existing
    ticket = session.scalar(
        select(Ticket)
        .where(Ticket.employee_id == employee.id, Ticket.status != "closed")
        .order_by(Ticket.created_at.desc(), Ticket.id.desc())
    )
    if not ticket:
        ticket = tickets.open_ticket(
            session, employee, "Ответ HR", {"initiated_by_hr": actor.id}, "hr-out:" + key
        )
    question = tickets.reply_question(session, employee, ticket, entire_thread=True)
    tickets.reply(session, actor, employee, ticket, text, key, question=question)
    return session.scalar(select(HRMessage).where(HRMessage.dedupe_key == "hr-out:" + key))
