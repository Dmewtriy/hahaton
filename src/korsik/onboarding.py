"""Onboarding ends independently of the employee's account and general activities."""

from zoneinfo import ZoneInfo

from sqlalchemy import or_, select

from .calendar import now
from .models import Outbox, ScheduledStep, SurveyRun


def past_end(session, employee):
    # The final day is inclusive, including final surveys and probation-end reminders.
    return now(session).astimezone(ZoneInfo(employee.timezone)).date() > employee.probation_end


def available(session, employee):
    return employee.onboarding_status == "active" and not past_end(session, employee)


def phase(session, employee):
    if employee.onboarding_status != "active":
        return employee.onboarding_status
    if past_end(session, employee):
        return "completed"
    if employee.status == "invited":
        return "invited"
    if now(session).astimezone(ZoneInfo(employee.timezone)).date() < employee.hire_date:
        return "preboarding"
    return "active"


def finish(session, employee, status="completed"):
    employee.onboarding_status = status
    employee.onboarding_ended_at = now(session)
    # Existing assignments survive natural completion (v0.4 P10).
    if status == "completed":
        from .models import RouteRun

        for step in session.scalars(
            select(ScheduledStep).where(
                ScheduledStep.employee_id == employee.id,
                ScheduledStep.campaign_id.is_(None),
                ScheduledStep.status.in_(["pending", "manual", "enqueued", "queued"]),
            )
        ):
            route = session.scalar(select(RouteRun).where(RouteRun.scheduled_step_id == step.id))
            if route:
                continue
            if step.payload["kind"] in {"survey", "flow"}:
                step.status = "late_review"
                step.last_error = "Адаптация завершена; запуск ранее назначенного шага требует решения HR"
            else:
                step.status = "cancelled"
        return
    steps = list(
        session.scalars(
            select(ScheduledStep).where(
                ScheduledStep.employee_id == employee.id, ScheduledStep.campaign_id.is_(None)
            )
        )
    )
    step_ids = {s.id for s in steps}
    from .models import RouteRun

    for route in session.scalars(
        select(RouteRun).where(RouteRun.scheduled_step_id.in_(step_ids), RouteRun.status != "completed")
    ):
        route.status = "cancelled"
    runs = list(session.scalars(select(SurveyRun).where(SurveyRun.scheduled_step_id.in_(step_ids))))
    run_ids = {r.id for r in runs}
    for step in steps:
        if step.status in {"pending", "manual", "enqueued", "queued"}:
            step.status = "cancelled"
    for run in runs:
        if run.status == "active":
            run.status = "expired" if status == "completed" else "cancelled"
    pending = employee.dialogue.get("awaiting_hr_message", {})
    if pending.get("step_id") in step_ids:
        employee.dialogue = {k: v for k, v in employee.dialogue.items() if k != "awaiting_hr_message"}
    for item in session.scalars(
        select(Outbox).where(
            Outbox.status.in_(["pending", "retry"]),
            or_(
                Outbox.recipient_id == employee.identity_id,
                Outbox.dedupe_key.in_(["step:" + sid for sid in step_ids]),
            ),
        )
    ):
        key = item.dedupe_key
        if (key.startswith("step:") and key[5:] in step_ids) or (
            key.split(":", 1)[0] in {"survey", "reminder"} and key.split(":")[1] in run_ids
        ):
            item.status = "cancelled"


def sync(session, employee):
    if employee.onboarding_status == "active" and past_end(session, employee):
        finish(session, employee)


def delivery_allowed(session, item):
    """Recheck at the final delivery boundary, including old, pre-upgrade outbox rows."""
    from .models import Employee
    from .scenarios import step_allowed

    key = item.dedupe_key
    step = None
    if key.startswith("step:"):
        step = session.get(ScheduledStep, key[5:])
        if step and step.status == "cancelled":
            return False
    elif key.startswith(("survey:", "reminder:")):
        run = session.get(SurveyRun, key.split(":")[1])
        if run:
            if run.status != "active":
                return False
            if run.expires_at and run.expires_at <= now(session):
                return False
            step = session.get(ScheduledStep, run.scheduled_step_id)
    if step:
        employee = session.get(Employee, step.employee_id)
        if not step_allowed(session, employee, step):
            return False
        if not step.campaign_id:
            if past_end(session, employee) and step.payload["kind"] == "message":
                return False
            return employee.onboarding_status != "stopped"
    return True
