"""Read-only role views. HR administration stays behind the Admin dependency."""

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from . import access, analytics, digests, meetings
from .dependencies import Actor, Session
from .models import DigestSnapshot, Employee, MeetingSignal

router = APIRouter(prefix="/api/workspace", tags=["Role workspace"])


@router.get("/team")
def team(session: Session, actor: Actor):
    access.require(bool(set(actor.roles) & {"hr", "hr_head", "manager"}))
    return analytics.dashboard(session, actor)


@router.get("/meetings")
def meeting_list(session: Session, actor: Actor):
    access.require(bool(set(actor.roles) & {"hr", "hr_head", "manager"}))
    result = []
    for signal in session.scalars(select(MeetingSignal).order_by(MeetingSignal.created_at.desc())):
        employee = session.get(Employee, signal.employee_id)
        if access.can_support(actor, employee) or (
            "manager" in actor.roles
            and employee.manager_id == actor.id
            and access.in_scope(actor, employee.branch_code)
        ):
            result.append(meetings.public_signal(signal, employee))
    return result


@router.get("/digests")
def digest_list(session: Session, actor: Actor):
    access.require("hr_head" in actor.roles)
    return [
        {
            "id": d.id,
            "window": d.report["window"],
            "created_at": d.created_at,
            "sample": d.report.get("sample", {}),
        }
        for d in session.scalars(
            select(DigestSnapshot)
            .where(DigestSnapshot.recipient_id == actor.id)
            .order_by(DigestSnapshot.created_at.desc())
            .limit(100)
        )
        if digests.can_read_snapshot(session, actor, d)
    ]


@router.get("/digests/{digest_id}")
def digest_detail(digest_id: str, session: Session, actor: Actor):
    access.require("hr_head" in actor.roles)
    snapshot = session.get(DigestSnapshot, digest_id)
    if not snapshot:
        raise HTTPException(404, "Сводка не найдена")
    access.require(digests.can_read_snapshot(session, actor, snapshot))
    return {
        "id": snapshot.id,
        "report": snapshot.report,
        "provenance": snapshot.provenance,
        "text": snapshot.text,
        "created_at": snapshot.created_at,
    }
