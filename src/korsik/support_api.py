"""Case-specific decisions: a resolution is closed only by its employee."""

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException
from sqlalchemy import select

from . import support
from .admin_service import audit
from .dependencies import Actor, Session
from .models import Employee, Ticket, TicketParticipant
from .schemas import TextInput

router = APIRouter(prefix="/api/support", tags=["Case resolutions"])


@router.get("/retained-cases")
def retained(session: Session, actor: Actor):
    from .access import can_support, is_hr, require

    require(is_hr(actor))
    result = []
    for ticket in session.scalars(
        select(Ticket)
        .join(TicketParticipant)
        .where(TicketParticipant.identity_id == actor.id)
        .order_by(Ticket.created_at.desc())
    ):
        employee = session.get(Employee, ticket.employee_id)
        if not can_support(actor, employee) and support.can_read(session, actor, ticket):
            result.append(
                {
                    "id": ticket.id,
                    "employee_name": employee.name,
                    "reason": ticket.reason,
                    "status": ticket.status,
                }
            )
    return result


@router.post("/{ticket_id}/resolution")
def resolve(
    ticket_id: str,
    data: TextInput,
    session: Session,
    actor: Actor,
    idempotency_key: Annotated[str, Header(min_length=1, max_length=120)],
):
    ticket = session.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(404, "Обращение не найдено")
    support.resolution(session, actor, ticket, data.text, f"{actor.id}:{ticket.id}:{idempotency_key}")
    audit(session, actor, "ticket.resolution", ticket.id)
    return {"status": ticket.status, "revision": ticket.resolution_revision}


@router.post("/{ticket_id}/close")
def close_case(
    ticket_id: str,
    session: Session,
    actor: Actor,
    idempotency_key: Annotated[str, Header(min_length=1, max_length=120)],
    data: TextInput | None = None,
):
    """Закрытие обращения HR или руководителем из веб-кабинета."""
    ticket = session.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(404, "Обращение не найдено")
    note = (data.text if data else "") or None
    support.close_by_staff(
        session,
        actor,
        ticket,
        f"{actor.id}:{ticket.id}:close:{idempotency_key}",
        note=note,
    )
    audit(session, actor, "ticket.staff_close", ticket.id)
    return {"status": ticket.status, "outcome": ticket.outcome}
