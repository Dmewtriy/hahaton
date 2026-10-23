from sqlalchemy import select

from .calendar import due_date, now
from .models import Branch, Scenario, ScheduledStep, SurveyRun, Ticket
from .queue import send
from .schemas import ScenarioDefinition


def rebuild_schedule(session, employee):
    branch = session.get(Branch, employee.branch_code)
    previous = list(session.scalars(select(ScheduledStep).where(ScheduledStep.employee_id == employee.id)))
    finished = {step.step_key for step in previous if step.status in {"dispatched", "completed"}}
    for step in previous:
        if step.status in {"pending", "manual", "queued", "enqueued"}:
            step.status = "cancelled"
    employee.schedule_revision += 1
    if not branch.scenario_id or not branch.active:
        return 0
    scenario = session.get(Scenario, branch.scenario_id)
    definition = ScenarioDefinition.model_validate(scenario.definition)
    surveys = {s.id: s.model_dump(mode="json") for s in definition.surveys}
    count = 0
    for step in definition.steps:
        if step.id in finished:
            continue
        if step.branch_codes and branch.code not in step.branch_codes:
            continue
        if step.categories and employee.category not in step.categories:
            continue
        payload = step.model_dump(mode="json")
        if step.survey_id:
            payload["survey"] = surveys[step.survey_id]
        session.add(
            ScheduledStep(
                employee_id=employee.id,
                step_key=step.id,
                scenario_id=scenario.id,
                revision=employee.schedule_revision,
                due_at=due_date(employee, branch, step.schedule),
                status="manual" if step.manual else "pending",
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
    human = session.scalar(
        select(Ticket.id).where(
            Ticket.employee_id == employee.id,
            Ticket.status != "closed",
            Ticket.human_mode.is_(True),
        )
    )
    return bool(survey or human)


def dispatch_step(session, employee, step):
    if step.status not in {"enqueued", "queued"} or not employee.active or employee.status != "active":
        return
    if step.revision != employee.schedule_revision:
        step.status = "cancelled"
        return
    if is_busy(session, employee):
        step.status = "queued"
        return
    from .surveys import start_survey

    branch = session.get(Branch, employee.branch_code)
    payload = step.payload
    if not branch.active:
        step.status = "cancelled"
        return
    text = (payload.get("informal_text") if employee.address_style == "informal" else None) or payload["text"]
    text = text.format(name=employee.name, branch=branch.name, department=employee.department)
    for key in payload.get("resource_keys", []):
        value = branch.resources.get(key)
        text += "\n" + (value or f"Материал «{key}» пока не добавлен. Обратитесь к рекрутеру.")
    recipient = employee.manager_id if payload["kind"] == "manager_reminder" else employee.identity_id
    if recipient:
        send(
            session,
            recipient,
            text,
            f"step:{step.id}",
            now(session),
            document_ids=payload.get("document_ids", []),
        )
    employee.stage = step.step_key
    step.status = "dispatched"
    if payload["kind"] == "survey":
        start_survey(session, employee, step)


def drain_dialogue_queue(session, employee):
    for step in session.scalars(
        select(ScheduledStep)
        .where(
            ScheduledStep.employee_id == employee.id,
            ScheduledStep.status == "queued",
        )
        .order_by(ScheduledStep.due_at, ScheduledStep.step_key)
    ):
        if is_busy(session, employee):
            break
        dispatch_step(session, employee, step)


def step_public(step):
    return {
        "id": step.id,
        "step_key": step.step_key,
        "due_at": step.due_at.isoformat(),
        "status": step.status,
        "revision": step.revision,
        "kind": step.payload["kind"],
    }
