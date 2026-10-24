from sqlalchemy import select

from .calendar import due_date, now
from .models import Branch, Campaign, CampaignRecipient, Scenario, ScheduledStep, SurveyRun
from .queue import send
from .schemas import ScenarioDefinition


def rebuild_schedule(session, employee):
    from . import onboarding

    onboarding.sync(session, employee)
    if not onboarding.available(session, employee):
        return 0
    branch = session.get(Branch, employee.branch_code)
    previous = list(
        session.scalars(
            select(ScheduledStep).where(
                ScheduledStep.employee_id == employee.id, ScheduledStep.campaign_id.is_(None)
            )
        )
    )
    from .models import RouteRun

    started_routes = set(
        session.scalars(
            select(RouteRun.scheduled_step_id).where(
                RouteRun.employee_id == employee.id, RouteRun.status != "cancelled"
            )
        )
    )
    finished = {
        step.step_key
        for step in previous
        if step.status in {"dispatched", "completed"} or step.id in started_routes
    }
    for step in previous:
        if step.id not in started_routes and step.status in {
            "pending",
            "manual",
            "queued",
            "enqueued",
            "waiting_material",
            "late_review",
        }:
            step.status = "cancelled"
    employee.schedule_revision += 1
    if not branch.scenario_id or not branch.active or not onboarding.available(session, employee):
        return 0
    scenario = session.get(Scenario, branch.scenario_id)
    definition = ScenarioDefinition.model_validate(scenario.definition)
    surveys = {s.id: s.model_dump(mode="json") for s in definition.surveys}
    count = 0
    for order_index, step in enumerate(definition.steps):
        if step.id in finished:
            continue
        if step.branch_codes and branch.code not in step.branch_codes:
            continue
        if step.categories and employee.category not in step.categories:
            continue
        payload = step.model_dump(mode="json")
        due_at = due_date(employee, branch, step.schedule)
        if branch.late_join_policy == "skip_past" and due_at < now(session):
            continue
        status = "manual" if step.manual else "pending"
        if branch.late_join_policy == "review" and due_at < now(session):
            status = "late_review"
        if step.survey_id:
            payload["survey"] = surveys[step.survey_id]
        if step.kind == "flow":
            payload["surveys"] = surveys
        session.add(
            ScheduledStep(
                employee_id=employee.id,
                step_key=step.id,
                scenario_id=scenario.id,
                revision=employee.schedule_revision,
                due_at=due_at,
                status=status,
                order_index=order_index,
                payload=payload,
            )
        )
        count += 1
    session.flush()
    return count


def is_busy(session, employee):
    survey = session.scalar(
        select(SurveyRun.id).where(
            SurveyRun.employee_id == employee.id,
            SurveyRun.status == "active",
        )
    )
    from .models import RouteRun

    route = session.scalar(
        select(RouteRun.id).where(
            RouteRun.employee_id == employee.id, RouteRun.status.in_(["active", "choice", "survey"])
        )
    )
    return bool(survey or route)


def dispatch_step(session, employee, step):
    from . import onboarding

    onboarding.sync(session, employee)
    if step.status not in {"enqueued", "queued"} or not employee.active or employee.status != "active":
        return
    if not step_allowed(session, employee, step):
        campaign = session.get(Campaign, step.campaign_id) if step.campaign_id else None
        step.status = (
            "expired"
            if campaign and campaign.expires_at and campaign.expires_at <= now(session)
            else "cancelled"
        )
        return
    if step.campaign_id:
        campaign = session.get(Campaign, step.campaign_id)
        if not campaign or campaign.status == "cancelled" or not campaign.active:
            step.status = "cancelled"
            return
        if campaign.expires_at and campaign.expires_at <= now(session):
            step.status = "expired"
            return
    elif employee.onboarding_status == "stopped":
        step.status = "cancelled"
        return
    if not step.campaign_id and step.revision != employee.schedule_revision:
        step.status = "cancelled"
        return
    if step.payload["kind"] != "manager_reminder" and is_busy(session, employee):
        step.status = "queued"
        return
    if step.payload["kind"] == "flow":
        from .routes import start

        start(session, employee, step)
        return
    from .surveys import start_survey

    branch = session.get(Branch, employee.branch_code)
    payload = step.payload
    if not branch.active:
        step.status = "cancelled"
        return
    text = (payload.get("informal_text") if employee.address_style == "informal" else None) or payload["text"]
    from .templates import render

    text = render(session, employee, text)
    for key in payload.get("resource_keys", []):
        value = branch.resources.get(key)
        if not value:
            step.status, step.last_error = "waiting_material", f"Не подготовлен материал: {key}"
            return
        text += "\n" + value
    recipient = employee.manager_id if payload["kind"] == "manager_reminder" else employee.identity_id
    if payload.get("reply_to") == "hr" and payload["kind"] == "message":
        text += (
            "\nВаше следующее сообщение получит HR. Для отмены: /cancel. "
            "Позже написать HR можно командой /hr ваше сообщение."
        )
    if recipient:
        notification = send(
            session,
            recipient,
            text,
            f"step:{step.id}",
            now(session),
            document_ids=payload.get("document_ids", []),
            buttons=[{"text": "Написать HR", "payload": "hr"}] if payload.get("reply_to") == "hr" else [],
        )
        if payload["kind"] == "manager_reminder":
            notification.body = {**notification.body, "manager_employee_id": employee.id}
        if payload.get("planning_id"):
            notification.body = {
                **notification.body,
                **{k: payload[k] for k in ("planning_id", "planning_revision", "planning_author_id")},
            }
        if payload.get("document_ids"):
            notification.body = {**notification.body, "required_document_ids": payload["document_ids"]}
    if not step.campaign_id and payload["kind"] != "manager_reminder":
        employee.stage = step.step_key
    step.status = "dispatched"
    if step.campaign_id:
        recipient = session.scalar(
            select(CampaignRecipient).where(CampaignRecipient.scheduled_step_id == step.id)
        )
        if recipient:
            recipient.status = "dispatched"
    if payload.get("reply_to") == "hr" and payload["kind"] == "message":
        employee.dialogue = {
            **employee.dialogue,
            "awaiting_hr_message": {"step_id": step.id, "survey_run_id": None},
        }
    if payload["kind"] == "survey":
        start_survey(session, employee, step)


def drain_dialogue_queue(session, employee):
    for step in session.scalars(
        select(ScheduledStep)
        .where(
            ScheduledStep.employee_id == employee.id,
            ScheduledStep.status == "queued",
        )
        .order_by(ScheduledStep.due_at, ScheduledStep.order_index, ScheduledStep.id)
    ):
        if is_busy(session, employee):
            break
        dispatch_step(session, employee, step)
        break  # At most one queued step per employee per scheduler pass.


def step_public(step):
    return {
        "id": step.id,
        "step_key": step.step_key,
        "due_at": step.due_at.isoformat(),
        "status": step.status,
        "revision": step.revision,
        "kind": step.payload["kind"],
    }


def step_allowed(session, employee, step):
    branch = session.get(Branch, employee.branch_code)
    if not branch or not branch.active:
        return False
    if step.campaign_id:
        campaign = session.get(Campaign, step.campaign_id)
        return bool(
            campaign
            and campaign.active
            and campaign.status != "cancelled"
            and (not campaign.expires_at or campaign.expires_at > now(session))
            and ("*" in campaign.branch_codes or employee.branch_code in campaign.branch_codes)
            and (
                not campaign.audience.get("categories")
                or employee.category in campaign.audience["categories"]
            )
        )
    scenario = session.get(Scenario, step.scenario_id)
    return bool(
        scenario
        and ("*" in scenario.branch_codes or employee.branch_code in scenario.branch_codes)
        and (not step.payload.get("branch_codes") or employee.branch_code in step.payload["branch_codes"])
        and (not step.payload.get("categories") or employee.category in step.payload["categories"])
    )


def stage_label(session, employee, *, step=None):
    from .onboarding import phase

    state = phase(session, employee)
    labels = {
        "completed": "Адаптация завершена",
        "stopped": "Адаптация остановлена",
        "invited": "Ожидает подключения",
    }
    if state in labels:
        return labels[state]
    if step is None:
        step = session.scalar(
            select(ScheduledStep)
            .where(
                ScheduledStep.employee_id == employee.id,
                ScheduledStep.step_key == employee.stage,
                ScheduledStep.campaign_id.is_(None),
            )
            .order_by(ScheduledStep.revision.desc())
            .limit(1)
        )
    if not step:
        return "До первого рабочего дня" if state == "preboarding" else "Ожидает шага адаптации"
    text = step.payload["text"].replace("{name}", employee.name).replace("{department}", employee.department)
    text = text.replace("{branch}", session.get(Branch, employee.branch_code).name)
    return text.splitlines()[0][:100]
