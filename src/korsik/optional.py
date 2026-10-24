"""Optional content and planning reuse documents, campaigns and the delivery queue."""

from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import select

from . import access, campaigns, documents
from .admin_schemas import AudienceInput, CampaignInput
from .admin_service import audit, revision_check
from .calendar import now
from .extension_models import EntityRevision, PlannedEvent
from .models import Campaign, Document, Identity
from .queue import send


def get_event(session, actor, event_id):
    event = session.scalar(select(PlannedEvent).where(PlannedEvent.id == event_id).with_for_update())
    if not event:
        raise HTTPException(404, "Событие не найдено")
    access.require(actor.active and access.is_hr(actor) and access.in_scope(actor, event.branch_code))
    return event


def event_dto(event):
    return {
        "id": event.id,
        "kind": event.kind,
        "title": event.title,
        "description": event.description,
        "branch_code": event.branch_code,
        "starts_at": event.starts_at.isoformat() if event.starts_at else None,
        "timezone": event.timezone,
        "location": event.location,
        "organizer_id": event.organizer_id,
        "audience": event.audience,
        "document_ids": event.document_ids,
        "status": event.status,
        "revision": event.revision,
    }


def save_event(session, actor, data, event_id=None):
    access.require(actor.active and access.is_hr(actor) and access.in_scope(actor, data.branch_code))
    from .models import Branch

    branch = session.get(Branch, data.branch_code)
    if not branch or not branch.active:
        raise HTTPException(422, "Выберите действующую площадку")
    members = campaigns.audience_members(session, actor, data.audience)
    if any(e.branch_code != data.branch_code for e in members):
        raise HTTPException(422, "Получатели должны относиться к площадке события")
    for doc_id in data.document_ids:
        doc = session.get(Document, doc_id)
        if not doc or doc.purpose == "personal_plan" or not documents.can_read(session, actor, doc):
            raise HTTPException(422, "Недоступное вложение события")
        if any(not documents.employee_can_read(e, doc) for e in members):
            raise HTTPException(422, "Вложение недоступно получателям")
    if event_id:
        event = get_event(session, actor, event_id)
        revision_check(event, data.revision)
        if event.kind != data.kind or event.branch_code != data.branch_code:
            raise HTTPException(409, "Вид и площадку записи менять нельзя")
        if event.status in {"cancelled", "archived", "held"}:
            raise HTTPException(409, "Запись завершена; создайте новую")
        # Invalidate every old notification in the same transaction as the change.
        for campaign in session.scalars(
            select(Campaign).where(
                Campaign.source_id == event.id,
                Campaign.source_kind == event.kind,
                Campaign.status != "draft",
                Campaign.status != "cancelled",
            )
        ):
            campaigns.cancel(session, actor, campaign)
        event.revision += 1
    else:
        event = PlannedEvent(organizer_id=actor.id)
        session.add(event)
    for field in (
        "kind",
        "title",
        "description",
        "branch_code",
        "starts_at",
        "timezone",
        "location",
        "document_ids",
        "status",
    ):
        setattr(event, field, getattr(data, field))
    event.audience = data.audience.model_dump()
    session.flush()
    session.add(
        EntityRevision(
            entity_id=event.id, revision=event.revision, actor_id=actor.id, snapshot=event_dto(event)
        )
    )
    audit(session, actor, "planning.save", event.id)
    return event


def announcement(session, actor, event, data):
    revision_check(event, data.revision)
    if event.status == "draft":
        raise HTTPException(409, "Сначала опубликуйте мероприятие или назначьте встречу")
    when = (
        event.starts_at.astimezone(ZoneInfo(event.timezone)).strftime("%d.%m.%Y %H:%M")
        if event.starts_at
        else "Дата уточняется"
    )
    prefix = "Встреча отменена: " if event.status == "cancelled" else ""
    text = f"{prefix}{event.title}\n{when} ({event.timezone})\n{event.location}\n{event.description}".strip()
    campaign = campaigns.save_campaign(
        session,
        actor,
        CampaignInput(
            title=event.title,
            kind="message",
            text=text,
            document_ids=event.document_ids,
            audience=AudienceInput.model_validate(event.audience),
            due_at=data.due_at,
        ),
    )
    campaign.source_kind, campaign.source_id, campaign.source_revision = event.kind, event.id, event.revision
    return campaign


def employee_events(session, employee, *, past=False):
    result = []
    for event in session.scalars(
        select(PlannedEvent)
        .where(
            PlannedEvent.kind == "event",
            PlannedEvent.status == "published",
            PlannedEvent.branch_code == employee.branch_code,
        )
        .order_by(PlannedEvent.starts_at, PlannedEvent.id)
    ):
        audience = event.audience
        allowed = (
            (audience["mode"] == "all")
            or (audience["mode"] == "branches" and employee.branch_code in audience["branch_codes"])
            or (audience["mode"] == "departments" and employee.department_id in audience["department_ids"])
            or (audience["mode"] == "employees" and employee.id in audience["employee_ids"])
        )
        if not allowed or (audience.get("categories") and employee.category not in audience["categories"]):
            continue
        is_past = bool(event.starts_at and event.starts_at < now(session))
        if is_past == past:
            result.append(event)
    return result


def plan(session, employee):
    return session.scalar(
        select(Document)
        .where(
            Document.purpose == "personal_plan",
            Document.owner_employee_id == employee.id,
            Document.active.is_(True),
            Document.published.is_(True),
        )
        .order_by(Document.version.desc())
    )


def send_plan(session, employee, key):
    doc = plan(session, employee)
    item = send(
        session,
        employee.identity_id,
        "Ваш адаптационный план" if doc else "Файл адаптационного плана пока не загружен.",
        "plan:" + key,
        now(session),
        document_ids=[doc.id] if doc else [],
    )
    if doc:
        item.body = {**item.body, "required_document_ids": [doc.id]}
    return item


def validate_source(session, campaign):
    if not campaign.source_id:
        return
    event = session.get(PlannedEvent, campaign.source_id)
    if not event or event.revision != campaign.source_revision:
        raise HTTPException(409, "Событие изменилось. Создайте и проверьте новое оповещение")


def delivery_allowed(session, item):
    source_id = item.body.get("planning_id")
    if not source_id:
        return True
    event = session.get(PlannedEvent, source_id)
    author = session.get(Identity, item.body.get("planning_author_id"))
    return bool(
        event
        and event.revision == item.body.get("planning_revision")
        and author
        and author.active
        and access.is_hr(author)
        and access.in_scope(author, event.branch_code)
    )


def store_attachment(
    session, settings, actor, *, title, filename, content, purpose, branch_code, employee_id=None
):
    access.require(actor.active and access.is_hr(actor) and access.in_scope(actor, branch_code))
    owner = None
    previous = None
    if purpose == "personal_plan":
        owner = access.employee_for(session, actor, employee_id, support=True)
        if owner.branch_code != branch_code:
            raise HTTPException(422, "Площадка плана должна совпадать с площадкой сотрудника")
        previous = plan(session, owner)
    elif employee_id:
        raise HTTPException(422, "Персональная привязка доступна только для плана")
    suffix = Path(filename).suffix.lower()
    if purpose == "card_image" and suffix not in {".jpg", ".jpeg", ".png"}:
        raise HTTPException(422, "Для открытки нужен PNG или JPEG")
    if suffix == ".png" and not content.startswith(b"\x89PNG\r\n\x1a\n"):
        raise HTTPException(422, "Некорректное изображение PNG")
    if suffix in {".jpg", ".jpeg"} and not content.startswith(b"\xff\xd8\xff"):
        raise HTTPException(422, "Некорректное изображение JPEG")
    doc = documents.store(
        session,
        settings,
        title,
        filename,
        content,
        [branch_code],
        [],
        (previous.version + 1) if previous else 1,
        previous=previous,
        purpose=purpose,
        owner_employee_id=owner.id if owner else None,
        created_by=actor.id,
    )
    audit(session, actor, "attachment.upload", doc.id)
    return doc
