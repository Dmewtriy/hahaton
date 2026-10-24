"""HTTP operations for optional scenarios and structured management work."""

from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import select

from . import access, campaigns, documents, management, optional
from .admin_service import audit
from .dependencies import Actor, Session, runtime_dependency
from .extension_models import AssignmentGrant, EntityRevision, PlannedEvent
from .extension_schemas import (
    AnnouncementInput,
    GrantInput,
    PlanDeliveryInput,
    PlannedEventInput,
    ReadInput,
    TaskCommand,
    TaskInput,
)
from .models import Branch, Employee, Identity


def optional_enabled(runtime=Depends(runtime_dependency)):
    if not runtime.settings.enable_optional:
        raise HTTPException(404, "Дополнительные сценарии отключены")


def management_enabled(runtime=Depends(runtime_dependency)):
    if not runtime.settings.enable_management:
        raise HTTPException(404, "Поручения отключены")


optional_router = APIRouter(
    prefix="/api/optional", tags=["Optional scenarios"], dependencies=[Depends(optional_enabled)]
)
management_router = APIRouter(
    prefix="/api/management", tags=["Management"], dependencies=[Depends(management_enabled)]
)


@optional_router.post("/attachments", status_code=201)
def upload_attachment(
    session: Session,
    actor: Actor,
    file: UploadFile = File(),
    title: str = Form(min_length=1, max_length=200),
    branch_code: str = Form(),
    purpose: Literal["personal_plan", "card_image", "event_attachment", "meeting_attachment"] = Form(),
    employee_id: str | None = Form(default=None),
    runtime=Depends(runtime_dependency),
):
    data = file.file.read(runtime.settings.max_upload_bytes + 1)
    doc = optional.store_attachment(
        session,
        runtime.settings,
        actor,
        title=title,
        filename=file.filename or "file",
        content=data,
        purpose=purpose,
        branch_code=branch_code,
        employee_id=employee_id,
    )
    return documents.public_document(doc)


@optional_router.get("/employees/{employee_id}/plan")
def employee_plan(employee_id: str, session: Session, actor: Actor):
    employee = access.employee_for(session, actor, employee_id)
    doc = optional.plan(session, employee)
    return {
        "document": documents.public_document(doc) if doc else None,
        "message": "" if doc else "Файл адаптационного плана пока не загружен.",
    }


@optional_router.get("/planning")
def list_planning(session: Session, actor: Actor, kind: Literal["event", "meeting"] | None = None):
    access.require(access.is_hr(actor))
    query = select(PlannedEvent).order_by(PlannedEvent.created_at.desc())
    if kind:
        query = query.where(PlannedEvent.kind == kind)
    return [optional.event_dto(x) for x in session.scalars(query) if access.in_scope(actor, x.branch_code)]


@optional_router.post("/employees/{employee_id}/plan/send")
def send_employee_plan(employee_id: str, data: PlanDeliveryInput, session: Session, actor: Actor):
    from .calendar import due_date, now
    from .queue import enqueue
    from .schemas import Schedule

    employee = access.employee_for(session, actor, employee_id, support=True)
    doc = optional.plan(session, employee)
    if not doc:
        raise HTTPException(409, "Файл адаптационного плана пока не загружен")
    if data.third_workday and employee.category != "office":
        raise HTTPException(422, "Правило третьего рабочего дня применяется к офисной категории")
    at = (
        due_date(employee, session.get(Branch, employee.branch_code), Schedule(working_day=3, at="09:00"))
        if data.third_workday
        else data.due_at or now(session)
    )
    job = enqueue(
        session,
        "personal_plan",
        {
            "employee_id": employee.id,
            "document_id": doc.id,
            "author_id": actor.id,
            "request_hash": management.digest(data.model_dump(mode="json")),
        },
        "personal-plan:" + actor.id + ":" + data.request_key,
        at,
    )
    if (
        job.payload.get("employee_id") != employee.id
        or job.payload.get("document_id") != doc.id
        or job.payload.get("request_hash") != management.digest(data.model_dump(mode="json"))
    ):
        raise HTTPException(409, "Ключ операции уже использован с другими данными")
    audit(session, actor, "plan.schedule", doc.id)
    return {"job_id": job.id, "due_at": job.due_at, "document_id": doc.id}


@optional_router.post("/planning", status_code=201)
def create_planning(data: PlannedEventInput, session: Session, actor: Actor):
    return optional.event_dto(optional.save_event(session, actor, data))


@optional_router.put("/planning/{event_id}")
def edit_planning(event_id: str, data: PlannedEventInput, session: Session, actor: Actor):
    return optional.event_dto(optional.save_event(session, actor, data, event_id))


@optional_router.get("/planning/{event_id}/history")
def planning_history(event_id: str, session: Session, actor: Actor, after: int = Query(default=0, ge=0)):
    optional.get_event(session, actor, event_id)
    return [
        {"revision": r.revision, "actor_id": r.actor_id, "snapshot": r.snapshot, "created_at": r.created_at}
        for r in session.scalars(
            select(EntityRevision)
            .where(
                EntityRevision.entity_id == event_id,
                EntityRevision.revision > after,
            )
            .order_by(EntityRevision.revision)
            .limit(50)
        )
    ]


@optional_router.post("/planning/{event_id}/announcement", status_code=201)
def planning_announcement(event_id: str, data: AnnouncementInput, session: Session, actor: Actor):
    event = optional.get_event(session, actor, event_id)
    campaign = optional.announcement(session, actor, event, data)
    return campaigns.campaign_dto(session, campaign)


@optional_router.get("/events")
def my_events(session: Session, actor: Actor, past: bool = False):
    employee = session.scalar(select(Employee).where(Employee.identity_id == actor.id))
    access.require(bool(employee and employee.active and "employee" in actor.roles))
    return [
        {
            k: v
            for k, v in optional.event_dto(e).items()
            if k
            in {
                "id",
                "kind",
                "title",
                "description",
                "branch_code",
                "starts_at",
                "timezone",
                "location",
                "document_ids",
            }
        }
        for e in optional.employee_events(session, employee, past=past)
    ]


@management_router.get("/capabilities")
def capabilities(session: Session, actor: Actor):
    management.staff(actor)
    return {
        "can_assign": "hr_head" in actor.roles,
        "can_read_improvements": "hr_head" in actor.roles,
        "can_manage_grants": actor.manage_access,
    }


@management_router.get("/grants")
def grants(session: Session, actor: Actor):
    access.require(actor.manage_access)
    return [
        {
            "id": g.id,
            "issuer_id": g.issuer_id,
            "assignee_id": g.assignee_id,
            "branch_code": g.branch_code,
            "active": g.active,
        }
        for g in session.scalars(select(AssignmentGrant))
        if access.in_scope(actor, g.branch_code)
    ]


@management_router.post("/grants")
def save_grant(data: GrantInput, session: Session, actor: Actor):
    access.require(actor.manage_access and access.in_scope(actor, data.branch_code))
    issuer = session.get(Identity, data.issuer_id)
    session.scalar(select(Identity).where(Identity.id == data.issuer_id).with_for_update())
    assignee = session.get(Identity, data.assignee_id)
    branch = session.get(Branch, data.branch_code)
    if (
        not issuer
        or not assignee
        or issuer.id == assignee.id
        or not issuer.active
        or not assignee.active
        or "hr_head" not in issuer.roles
        or not access.is_hr(assignee)
        or not branch
        or not branch.active
        or not access.in_scope(issuer, branch.code)
        or not access.in_scope(assignee, branch.code)
    ):
        raise HTTPException(422, "Проверьте роли, активность, разные учётные записи и общую область")
    grant = session.scalar(
        select(AssignmentGrant)
        .where(
            AssignmentGrant.issuer_id == issuer.id,
            AssignmentGrant.assignee_id == assignee.id,
            AssignmentGrant.branch_code == branch.code,
        )
        .with_for_update()
    )
    if not grant:
        grant = AssignmentGrant(
            issuer_id=issuer.id, assignee_id=assignee.id, branch_code=branch.code, created_by=actor.id
        )
        session.add(grant)
    grant.active = data.active
    session.flush()
    audit(session, actor, "management.grant", grant.id)
    return {"id": grant.id, "active": grant.active}


@management_router.get("/assignees")
def allowed_assignees(session: Session, actor: Actor):
    return management.assignees(session, actor)


@management_router.post("/tasks", status_code=201)
def new_task(data: TaskInput, session: Session, actor: Actor, runtime=Depends(runtime_dependency)):
    return management.task_dto(session, actor, management.create_task(session, actor, data, runtime.settings))


@management_router.get("/tasks")
def tasks(
    session: Session,
    actor: Actor,
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
):
    rows = management.visible_tasks(session, actor)
    return {
        "items": [management.task_dto(session, actor, t) for t in rows[after : after + limit]],
        "next_after": after + limit if len(rows) > after + limit else None,
    }


@management_router.get("/tasks/{task_id}")
def task_detail(task_id: str, session: Session, actor: Actor):
    return management.task_dto(session, actor, management.get_task(session, actor, task_id))


@management_router.post("/tasks/{task_id}/commands")
def task_command(
    task_id: str, data: TaskCommand, session: Session, actor: Actor, runtime=Depends(runtime_dependency)
):
    task = management.get_task(session, actor, task_id)
    return management.task_dto(
        session, actor, management.command(session, actor, task, data, runtime.settings)
    )


@management_router.get("/conversations")
def conversations(session: Session, actor: Actor):
    return management.summaries(session, actor)


@management_router.get("/summary")
def summary(session: Session, actor: Actor):
    rows = management.summaries(session, actor)
    return {
        "unread_count": sum(r["unread_count"] for r in rows),
        "unread_conversations": sum(bool(r["unread_count"]) for r in rows),
    }


@management_router.get("/conversations/{conversation_id}/entries")
def entries(
    conversation_id: str,
    session: Session,
    actor: Actor,
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
):
    return management.conversation_entries(session, actor, conversation_id, after=after, limit=limit)


@management_router.post("/conversations/{conversation_id}/read")
def read_conversation(conversation_id: str, data: ReadInput, session: Session, actor: Actor):
    return management.mark_read(session, actor, conversation_id, data.through_seq)
