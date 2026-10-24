"""HR inbox HTTP contract: assignment-filtered list, history and independent read state."""

from typing import Annotated, Literal

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import Field
from sqlalchemy import select

from . import inbox
from .admin_service import audit
from .calendar import now
from .dependencies import Admin, Session
from .models import Identity, Ticket, uid
from .schemas import StrictModel, TextInput

router = APIRouter(prefix="/api/admin/inbox", tags=["HR Inbox"])


class ReadInput(StrictModel):
    last_message_id: int = Field(ge=1)


@router.get("")
def list_threads(
    session: Session, actor: Admin, scope: Literal["mine", "all"] = "mine", archived: bool = False
):
    return inbox.threads(session, actor, scope, archived)


@router.get("/summary")
def unread_summary(session: Session, actor: Admin):
    rows = inbox.threads(session, actor)
    return {
        "unread_threads": sum(row["unread_count"] > 0 for row in rows),
        "unread_messages": sum(row["unread_count"] for row in rows),
    }


@router.get("/{employee_id}")
def thread(
    employee_id: str,
    session: Session,
    actor: Admin,
    before: int = Query(default=0, ge=0),
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
):
    if before and after:
        raise HTTPException(422, "Используйте before или after")
    return inbox.history(session, actor, employee_id, before=before, after=after, limit=limit)


@router.post("/{employee_id}/read")
def read(employee_id: str, data: ReadInput, session: Session, actor: Admin):
    inbox.mark_read(session, actor, employee_id, data.last_message_id)
    return {"ok": True}


@router.post("/{employee_id}/messages", status_code=201)
def reply(
    employee_id: str,
    data: TextInput,
    session: Session,
    actor: Admin,
    idempotency_key: Annotated[str, Header(min_length=1, max_length=120)],
):
    employee = inbox.employee_thread(session, actor, employee_id)
    if not employee.active or not session.get(Identity, employee.identity_id).active:
        raise HTTPException(409, "Сначала восстановите доступ сотрудника")
    message = inbox.reply(
        session, actor, employee, data.text, f"inbox:{actor.id}:{employee.id}:{idempotency_key}"
    )
    audit(session, actor, "inbox.reply", employee.id)
    return {"message_id": message.id}


@router.post("/{employee_id}/resolve")
def resolve(employee_id: str, session: Session, actor: Admin):
    employee = inbox.employee_thread(session, actor, employee_id)
    opened = list(
        session.scalars(select(Ticket).where(Ticket.employee_id == employee_id, Ticket.status != "closed"))
    )
    for ticket in opened:
        ticket.status, ticket.human_mode, ticket.closed_at = "closed", False, now(session)
    if opened:
        message = inbox.append(
            session, employee, None, "system", "HR отметил обращения решёнными.", "resolve:" + uid(), actor.id
        )
        inbox.mark_read(session, actor, employee.id, message.id)
    audit(session, actor, "inbox.resolve", employee.id)
    return {"resolved": len(opened)}
