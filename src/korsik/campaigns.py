"""HR broadcasts and surveys: immutable publication snapshots, scoped audiences and cancellation."""

import hashlib
from collections import Counter

from fastapi import HTTPException
from sqlalchemy import select

from . import access, documents
from .admin_service import audit, check_scope, employee_dto, revision_check, visible_scoped
from .analytics import visible_employees
from .calendar import now
from .models import (
    Branch,
    Campaign,
    CampaignRecipient,
    Department,
    Document,
    Employee,
    Identity,
    Outbox,
    Scenario,
    ScheduledStep,
    SurveyRun,
    SurveyTemplate,
)


def audience_members(session, actor, audience):
    access.require(access.is_hr(actor))
    selected = visible_employees(session, actor)
    if audience.mode == "branches":
        check_scope(actor, audience.branch_codes)
        for code in audience.branch_codes:
            branch = session.get(Branch, code)
            if not branch or not branch.active:
                raise HTTPException(422, "Выбрана недоступная площадка")
        selected = [e for e in selected if e.branch_code in audience.branch_codes]
    elif audience.mode == "departments":
        for department_id in audience.department_ids:
            department = session.get(Department, department_id)
            if not department or not department.active:
                raise HTTPException(422, "Выбрано недоступное подразделение")
            check_scope(actor, [department.branch_code])
        selected = [e for e in selected if e.department_id in audience.department_ids]
    elif audience.mode == "employees":
        for employee_id in audience.employee_ids:
            access.employee_for(session, actor, employee_id, support=True)
        selected = [e for e in selected if e.id in audience.employee_ids]
    if audience.categories:
        selected = [e for e in selected if e.category in audience.categories]
    return [
        e
        for e in selected
        if e.active
        and e.status != "stopped"
        and session.get(Identity, e.identity_id).active
        and session.get(Branch, e.branch_code).active
    ]


def preview_audience(session, actor, audience):
    members = audience_members(session, actor, audience)
    return {
        "audience_hash": audience_hash(members),
        "count": len(members),
        "connected": sum(bool(session.get(Identity, e.identity_id).max_user_id) for e in members),
        "employees": [employee_dto(session, e) for e in members[:200]],
        "truncated": len(members) > 200,
    }


def get_campaign(session, actor, campaign_id):
    campaign = session.scalar(select(Campaign).where(Campaign.id == campaign_id).with_for_update())
    if not campaign:
        raise HTTPException(404, "Рассылка не найдена")
    check_scope(actor, campaign.branch_codes)
    # An employee transfer must not reveal their new department or later answers
    # to the HR who administered their previous branch.
    for employee in session.scalars(
        select(Employee)
        .join(
            CampaignRecipient,
            CampaignRecipient.employee_id == Employee.id,
        )
        .where(CampaignRecipient.campaign_id == campaign.id)
    ):
        access.require(access.can_support(actor, employee))
    return campaign


def save_campaign(session, actor, data, campaign_id=None):
    members = audience_members(session, actor, data.audience)
    scope = sorted({e.branch_code for e in members}) or list(actor.branch_codes)
    if not scope:
        raise HTTPException(422, "Нет доступной области рассылки")
    if campaign_id:
        campaign = get_campaign(session, actor, campaign_id)
        revision_check(campaign, data.revision)
        if campaign.status != "draft":
            raise HTTPException(409, "Опубликованную рассылку нельзя изменить. Создайте копию")
        campaign.revision += 1
    else:
        campaign = Campaign(created_by=actor.id)
    snapshot = None
    if data.kind == "survey":
        template = session.get(SurveyTemplate, data.template_id)
        if not template or not template.active or not visible_scoped(actor, template.branch_codes):
            raise HTTPException(422, "Опросник недоступен")
        if any(
            "*" not in template.branch_codes and e.branch_code not in template.branch_codes for e in members
        ):
            raise HTTPException(422, "Опросник не разрешён части выбранных площадок")
        snapshot = template.definition
    for doc_id in data.document_ids:
        doc = session.get(Document, doc_id)
        if not doc or doc.purpose == "personal_plan" or not documents.can_read(session, actor, doc) or not doc.active:
            raise HTTPException(422, "Вложение недоступно")
        if any(not documents.employee_can_read(e, doc) for e in members):
            raise HTTPException(422, "Вложение недоступно части выбранных сотрудников")
    if data.message_type == "first_day_card" and not any(
        session.get(Document, d).purpose == "card_image" for d in data.document_ids
    ):
        raise HTTPException(422, "Для открытки первого дня необходимо изображение")
    campaign.title, campaign.kind, campaign.text = data.title, data.kind, data.text
    campaign.document_ids, campaign.template_id = data.document_ids, data.template_id
    campaign.message_type = data.message_type
    campaign.survey_snapshot = snapshot
    campaign.audience = data.audience.model_dump()
    campaign.branch_codes = scope
    campaign.due_at = data.due_at or now(session)
    campaign.expires_at = data.expires_at
    if campaign.expires_at and campaign.expires_at <= campaign.due_at:
        raise HTTPException(422, "Срок прохождения должен быть позже времени отправки")
    session.add(campaign)
    session.flush()
    audit(session, actor, "campaign.save", campaign.id)
    return campaign


def audience_hash(members):
    return hashlib.sha256("\n".join(sorted(e.id for e in members)).encode()).hexdigest()


def publish(session, actor, campaign, expected_hash):
    from .admin_schemas import AudienceInput
    from .optional import validate_source

    validate_source(session, campaign)

    if campaign.status != "draft" or not campaign.active:
        raise HTTPException(409, "Эта рассылка уже опубликована или находится в архиве")
    if campaign.expires_at and campaign.expires_at <= now(session):
        raise HTTPException(409, "Срок рассылки уже истёк. Измените черновик")
    members = audience_members(session, actor, AudienceInput.model_validate(campaign.audience))
    if not members:
        raise HTTPException(422, "Нет доступных получателей")
    if audience_hash(members) != expected_hash:
        raise HTTPException(409, "Состав получателей изменился. Обновите предпросмотр перед отправкой")
    if campaign.kind == "survey":
        template = session.get(SurveyTemplate, campaign.template_id)
        if not template or not template.active:
            raise HTTPException(409, "Опросник был удалён. Обновите черновик")
        if any(
            "*" not in template.branch_codes and e.branch_code not in template.branch_codes for e in members
        ):
            raise HTTPException(409, "Область доступа опросника изменилась. Обновите черновик")
    for doc_id in campaign.document_ids:
        doc = session.get(Document, doc_id)
        if not doc or not doc.active or doc.purpose == "personal_plan" or any(not documents.employee_can_read(e, doc) for e in members):
            raise HTTPException(409, "Вложение больше недоступно выбранным получателям")
    if campaign.message_type == "first_day_card" and not any(
        session.get(Document, d).purpose == "card_image" for d in campaign.document_ids
    ):
        raise HTTPException(409, "Изображение открытки недоступно")
    # Preserve exactly the reviewed questions, not whatever the template contains at send time.
    definition = {
        "name": "Рассылка " + campaign.id,
        "version": 1,
        "steps": [],
        "surveys": [campaign.survey_snapshot] if campaign.survey_snapshot else [],
    }
    scenario = Scenario(
        name=definition["name"],
        version=1,
        definition=definition,
        kind="campaign",
        branch_codes=campaign.branch_codes,
    )
    session.add(scenario)
    session.flush()
    campaign.scenario_id = scenario.id
    for employee in members:
        payload = {
            "kind": campaign.kind,
            "text": campaign.text,
            "document_ids": campaign.document_ids,
            "resource_keys": [],
            "campaign_title": campaign.title,
            "planning_id": campaign.source_id,
            "planning_revision": campaign.source_revision,
            "planning_author_id": campaign.created_by,
            "expires_at": campaign.expires_at.isoformat() if campaign.expires_at else None,
        }
        if campaign.survey_snapshot:
            payload["survey"] = campaign.survey_snapshot
        step = ScheduledStep(
            employee_id=employee.id,
            step_key="campaign_" + campaign.id,
            scenario_id=scenario.id,
            campaign_id=campaign.id,
            revision=1,
            due_at=campaign.due_at,
            payload=payload,
        )
        session.add(step)
        session.flush()
        session.add(
            CampaignRecipient(campaign_id=campaign.id, employee_id=employee.id, scheduled_step_id=step.id)
        )
    campaign.status, campaign.published_at = "scheduled", now(session)
    campaign.revision += 1
    audit(session, actor, "campaign.publish", campaign.id)
    return campaign


def cancel(session, actor, campaign):
    if campaign.status == "cancelled":
        return campaign
    campaign.status = "cancelled"
    campaign.revision += 1
    steps = list(session.scalars(select(ScheduledStep).where(ScheduledStep.campaign_id == campaign.id)))
    for step in steps:
        if step.status in {"pending", "manual", "enqueued", "queued"}:
            step.status = "cancelled"
        run = session.scalar(select(SurveyRun).where(SurveyRun.scheduled_step_id == step.id))
        if run and run.status == "active":
            run.status = "cancelled"
        for item in session.scalars(
            select(Outbox).where(
                Outbox.recipient_id == session.get(Employee, step.employee_id).identity_id,
                Outbox.status.in_(["pending", "retry"]),
            )
        ):
            if item.dedupe_key == "step:" + step.id or (
                run
                and (
                    item.dedupe_key.startswith("survey:" + run.id + ":")
                    or item.dedupe_key.startswith("reminder:" + run.id + ":")
                )
            ):
                item.status = "cancelled"
        recipient = session.scalar(
            select(CampaignRecipient).where(CampaignRecipient.scheduled_step_id == step.id)
        )
        if recipient and recipient.status != "completed":
            recipient.status = "cancelled"
    session.flush()
    from .scenarios import drain_dialogue_queue

    for employee_id in {s.employee_id for s in steps}:
        drain_dialogue_queue(session, session.get(Employee, employee_id))
    audit(session, actor, "campaign.cancel", campaign.id)
    return campaign


def expire_assignments(session, at):
    """Expire even unstarted assignments of users who have not accepted an invitation."""
    for step in session.scalars(
        select(ScheduledStep)
        .join(Campaign)
        .where(
            Campaign.expires_at <= at,
            Campaign.status != "cancelled",
            ScheduledStep.status.in_(["pending", "manual", "enqueued", "queued", "dispatched"]),
        )
    ):
        delivery = session.scalar(select(Outbox).where(Outbox.dedupe_key == "step:" + step.id))
        if (
            step.payload["kind"] == "message"
            and delivery
            and delivery.status in {"sent", "uncertain", "sending"}
        ):
            continue
        step.status = "expired"
        recipient = session.scalar(
            select(CampaignRecipient).where(CampaignRecipient.scheduled_step_id == step.id)
        )
        if recipient:
            recipient.status = "expired"


def campaign_dto(session, campaign, include_recipients=False):
    recipients = list(
        session.scalars(select(CampaignRecipient).where(CampaignRecipient.campaign_id == campaign.id))
    )
    delivery_counts = Counter()
    rows = []
    for recipient in recipients:
        step = session.get(ScheduledStep, recipient.scheduled_step_id)
        delivery = session.scalar(select(Outbox).where(Outbox.dedupe_key == "step:" + step.id))
        run = session.scalar(select(SurveyRun).where(SurveyRun.scheduled_step_id == step.id))
        status = (
            "completed"
            if run and run.status == "completed"
            else (run.status if run else delivery.status if delivery else step.status)
        )
        delivery_counts[status] += 1
        if include_recipients:
            employee = session.get(Employee, recipient.employee_id)
            rows.append(
                {
                    "employee_id": employee.id,
                    "name": employee.name,
                    "department": employee.department,
                    "branch_code": employee.branch_code,
                    "status": status,
                    "survey_run_id": run.id if run else None,
                    "result": run.result if run else None,
                }
            )
    status = campaign.status
    if recipients and status != "cancelled":
        if all(k in {"completed", "sent"} for k in delivery_counts):
            status = "completed"
        elif all(k in {"completed", "sent", "expired", "cancelled", "failed"} for k in delivery_counts):
            status = (
                "expired"
                if "expired" in delivery_counts
                else "failed"
                if "failed" in delivery_counts
                else "cancelled"
            )
        elif any(k in {"active", "sent", "completed", "dispatched"} for k in delivery_counts):
            status = "sending"
    result = {
        "id": campaign.id,
        "title": campaign.title,
        "kind": campaign.kind,
        "message_type": campaign.message_type,
        "source_kind": campaign.source_kind,
        "source_id": campaign.source_id,
        "source_revision": campaign.source_revision,
        "text": campaign.text,
        "document_ids": campaign.document_ids,
        "template_id": campaign.template_id,
        "audience": campaign.audience,
        "due_at": campaign.due_at.isoformat(),
        "status": status,
        "stored_status": campaign.status,
        "active": campaign.active,
        "revision": campaign.revision,
        "recipient_count": len(recipients),
        "progress": dict(delivery_counts),
        "published_at": campaign.published_at,
        "expires_at": campaign.expires_at.isoformat() if campaign.expires_at else None,
    }
    if include_recipients:
        result["recipients"] = rows
    return result
