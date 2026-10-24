"""Persistent, ordered source-driven onboarding flows and required material delivery."""

from sqlalchemy import select

from . import documents
from .calendar import now
from .models import Document, Employee, Material, RouteRun, ScheduledStep, SurveyRun
from .queue import send
from .templates import render


def document_matches(material, doc):
    key = material.key.split(":", 1)[0]
    suffix = doc.original_name.lower().rsplit(".", 1)[-1]
    if key.startswith("MAT-"):
        return suffix in {"png", "jpg", "jpeg"}
    if key in {"RES-R026", "RES-R027", "RES-R028"}:
        return suffix == "pdf"
    return True


def resolve_material(session, employee, key):
    from fastapi import HTTPException

    material = session.get(Material, key)
    if not material or not ("*" in material.branch_codes or employee.branch_code in material.branch_codes):
        raise HTTPException(409, f"Материал {key} не подготовлен для площадки")
    if material.document_id:
        doc = session.get(Document, material.document_id)
        folder = session.info.get("data_dir")
        if (
            doc
            and documents.employee_can_read(employee, doc)
            and document_matches(material, doc)
            and doc.status in {"ready", "ready_stub"}
            and (not folder or (folder / "documents" / doc.storage_name).is_file())
        ):
            return {"document_ids": [doc.id], "text": ""}
    if (
        material.url
        and material.url.startswith(("https://", "http://"))
        and key.split(":", 1)[0] not in {"RES-R026", "RES-R027", "RES-R028"}
        and not key.startswith("MAT-")
    ):
        return {"document_ids": [], "text": material.url}
    raise HTTPException(409, f"Не загружен обязательный материал: {material.title}")


def active(session, employee):
    return session.scalar(
        select(RouteRun)
        .where(RouteRun.employee_id == employee.id, RouteRun.status == "choice")
        .with_for_update()
    )


def start(session, employee, step):
    run = session.scalar(select(RouteRun).where(RouteRun.scheduled_step_id == step.id))
    if not run:
        run = RouteRun(
            employee_id=employee.id,
            scheduled_step_id=step.id,
            definition=step.payload["actions"],
            created_at=now(session),
        )
        session.add(run)
        session.flush()
    step.status = "dispatched"
    employee.stage = step.step_key
    advance(session, employee, run)


def matches(employee, run, node):
    if node.get("branch_codes") and employee.branch_code not in node["branch_codes"]:
        return False
    if node.get("categories") and employee.category not in node["categories"]:
        return False
    condition = node.get("when")
    if condition:
        value = run.answers.get(condition["choice"], {}).get("value")
        if value not in condition.get("in", [condition.get("equals")]):
            return False
    return True


def advance(session, employee, run):
    from fastapi import HTTPException

    from . import surveys

    step = session.get(ScheduledStep, run.scheduled_step_id)
    from .scenarios import step_allowed

    if (
        not employee.active
        or employee.onboarding_status == "stopped"
        or not step_allowed(session, employee, step)
    ):
        run.status = "cancelled"
        return
    run.status = "active"
    run.last_error = None
    while run.position < len(run.definition):
        node = run.definition[run.position]
        if not matches(employee, run, node):
            scope_skip = bool(
                node.get("branch_codes")
                and employee.branch_code not in node["branch_codes"]
                or node.get("categories")
                and employee.category not in node["categories"]
            )
            run.trace = [
                *run.trace,
                {
                    "position": run.position,
                    "node_id": node["id"],
                    "source_ref": node.get("source_ref"),
                    "status": "scope_not_applicable" if scope_skip else "condition_not_met",
                    "branch_code": employee.branch_code,
                    "category": employee.category,
                    "at": now(session).isoformat(),
                },
            ]
            run.position += 1
            continue
        key = f"route:{run.id}:{run.position}"
        text = render(session, employee, node.get("text", ""))
        if node["kind"] == "wait":
            from datetime import datetime, time
            from zoneinfo import ZoneInfo

            local = step.due_at.astimezone(ZoneInfo(employee.timezone))
            boundary = datetime.combine(local.date(), time.fromisoformat(node["at"]), local.tzinfo)
            if now(session) < boundary:
                run.status = "timed_wait"
                return
            run.position += 1
            continue
        if node["kind"] == "choice":
            run.status = "choice"
            item = send(
                session,
                employee.identity_id,
                text,
                key,
                now(session),
                buttons=[
                    {"text": o["label"], "payload": f"r:{run.id}:{run.position}:{o['id']}"}
                    for o in node["options"]
                ],
            )
            item.body = {**item.body, "route_run_id": run.id, "route_node": run.position}
            if item.status == "blocked_material":
                item.status = "pending"
            return
        if node["kind"] == "survey":
            existing = session.scalar(select(SurveyRun).where(SurveyRun.scheduled_step_id == step.id))
            if existing:
                if existing.status == "active":
                    run.status = "survey"
                    return
            else:
                if text:
                    send(session, employee.identity_id, text, key, now(session))
                step.payload = {**step.payload, "survey": step.payload["surveys"][node["survey_id"]]}
                surveys.start_survey(session, employee, step)
                run.status = "survey"
                return
        else:
            ids = []
            body = text
            try:
                if node.get("material_key"):
                    value = resolve_material(session, employee, node["material_key"])
                    ids = value["document_ids"]
                    body += ("\n" if body and value["text"] else "") + value["text"]
                if node["kind"] == "employee_resource":
                    url = getattr(employee, node["field"])
                    if not url:
                        raise HTTPException(409, "Не подготовлено: " + node["title"])
                    body += ("\n" if body else "") + url
            except HTTPException as error:
                run.status = "blocked_material"
                run.last_error = str(error.detail)
                step.status = "waiting_material"
                step.last_error = run.last_error
                return
            if body or ids:
                item = send(
                    session,
                    employee.identity_id,
                    body,
                    key,
                    now(session),
                    document_ids=ids,
                    buttons=node.get("buttons", []),
                )
                item.body = {
                    **item.body,
                    "required_document_ids": ids,
                    "route_run_id": run.id,
                    "route_node": run.position,
                }
                if item.status in {"blocked_material", "cancelled"}:
                    item.status = "pending"
                    item.retry_at = now(session)
        run.position += 1
    run.status = "completed"
    step.status = "completed"
    step.last_error = None
    employee.stage = step.step_key


def consume(session, employee, text, key, payload=None):
    run = active(session, employee)
    if not run:
        return False
    if payload:
        parts = payload.split(":")
        if len(parts) != 4 or parts[1] != run.id or parts[2] != str(run.position):
            send(
                session,
                employee.identity_id,
                "Кнопка устарела. Продолжить: /resume.",
                "route-stale:" + key,
                now(session),
            )
            return True
        text = parts[3]
    node = run.definition[run.position]
    options = node["options"]
    selected = next((o for o in options if o["id"] == text or o["label"].casefold() == text.casefold()), None)
    if not selected and text.isascii() and text.isdigit() and 1 <= int(text) <= len(options):
        selected = options[int(text) - 1]
    if not selected:
        # A fresh event can redraw the same state without duplicating its original message.
        send(
            session,
            employee.identity_id,
            render(session, employee, node["text"]),
            "route-prompt:" + key,
            now(session),
            buttons=[
                {"text": o["label"], "payload": f"r:{run.id}:{run.position}:{o['id']}"} for o in options
            ],
        )
        return True
    run.answers = {
        **run.answers,
        node["id"]: {
            "value": selected["id"],
            "source_ref": node.get("source_ref"),
            "at": now(session).isoformat(),
        },
    }
    if selected.get("set_style"):
        employee.address_style = selected["set_style"]
    if selected.get("reply"):
        send(
            session,
            employee.identity_id,
            render(session, employee, selected["reply"]),
            "route-reply:" + key,
            now(session),
        )
    run.position += 1
    advance(session, employee, run)
    return True


def survey_completed(session, employee, survey):
    run = session.scalar(select(RouteRun).where(RouteRun.scheduled_step_id == survey.scheduled_step_id))
    if run and run.status == "survey":
        session.get(ScheduledStep, run.scheduled_step_id).status = "dispatched"
        run.position += 1
        advance(session, employee, run)


def retry_blocked(session):
    for run in session.scalars(
        select(RouteRun).where(RouteRun.status.in_(["blocked_material", "timed_wait"])).limit(100)
    ):
        employee = session.get(Employee, run.employee_id)
        from .scenarios import is_busy

        if not is_busy(session, employee):
            advance(session, employee, run)
    for step in session.scalars(
        select(ScheduledStep)
        .where(
            ScheduledStep.status == "waiting_material",
            ScheduledStep.id.not_in(select(RouteRun.scheduled_step_id)),
        )
        .limit(100)
    ):
        from .scenarios import dispatch_step

        step.status = "enqueued"
        dispatch_step(session, session.get(Employee, step.employee_id), step)


def delivery_allowed(session, item, employee):
    if not item.body.get("route_run_id"):
        return True
    run = session.get(RouteRun, item.body["route_run_id"])
    if not run or not employee or run.employee_id != employee.id or run.status == "cancelled":
        return False
    step = session.get(ScheduledStep, run.scheduled_step_id)
    from .scenarios import step_allowed

    return bool(
        employee.onboarding_status != "stopped"
        and step_allowed(session, employee, step)
        and step.status != "cancelled"
    )


def block_delivery(session, item):
    from .models import Outbox

    run = session.get(RouteRun, item.body.get("route_run_id")) if item.body.get("route_run_id") else None
    if not run:
        return
    run.position = min(run.position, item.body["route_node"])
    run.status = "blocked_material"
    run.last_error = "Файл обязательного материала недоступен"
    step = session.get(ScheduledStep, run.scheduled_step_id)
    step.status = "waiting_material"
    step.last_error = run.last_error
    for pending in session.scalars(
        select(Outbox).where(
            Outbox.recipient_id == item.recipient_id, Outbox.status.in_(["pending", "retry"])
        )
    ):
        if pending.body.get("route_run_id") == run.id and pending.body.get("route_node", 0) >= run.position:
            pending.status = "blocked_material"
