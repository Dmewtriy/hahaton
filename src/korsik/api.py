import json
import logging
import secrets
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.staticfiles import StaticFiles
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError

from . import access, analytics, documents, download_links, employees, scenarios, tickets
from .calendar import add_months, now
from .config import Settings
from .dependencies import Actor, Session, bearer
from .max_client import normalize_update
from .models import (
    AuditEvent,
    Branch,
    Document,
    Employee,
    FeedbackAnalysis,
    Identity,
    Job,
    Outbox,
    Scenario,
    ScheduledStep,
    State,
    SurveyAnswer,
    SurveyRun,
    Ticket,
    TicketMessage,
    WebSession,
    uid,
)
from .runtime import Runtime
from .schemas import (
    BranchInput,
    ClockInput,
    DemoEvent,
    DigestPolicy,
    EmployeeCreate,
    EmployeeDates,
    EscalationRules,
    IdentityCreate,
    IdentityUpdate,
    ScenarioDefinition,
    TextInput,
)
from .worker import Worker, ingest

log = logging.getLogger("korsik.api")


def create_app(runtime=None):
    runtime = runtime or Runtime(Settings())

    @asynccontextmanager
    async def lifespan(app):
        runtime.bootstrap()
        yield
        runtime.close()

    app = FastAPI(title="Корсик: backend", version="0.1.0", lifespan=lifespan)
    app.state.runtime = runtime
    from .extension_api import management_router, optional_router
    from .improvements_api import router as improvements_router

    app.include_router(optional_router)
    app.include_router(management_router)
    app.include_router(improvements_router)
    from .admin_api import router as admin_router

    app.include_router(admin_router)
    from .inbox_api import router as inbox_router

    app.include_router(inbox_router)
    from .workspace_api import router as workspace_router

    app.include_router(workspace_router)
    from .feedback_api import router as feedback_router
    from .nlp_api import router as nlp_router
    from .report_api import router as report_router
    from .route_api import router as route_router
    from .support_api import router as support_router

    for router in (report_router, route_router, support_router, nlp_router, feedback_router):
        app.include_router(router)
    app.mount("/admin-assets", StaticFiles(directory=Path(__file__).parent / "web"), name="admin-assets")

    @app.middleware("http")
    async def technical_logs(request: Request, call_next):
        request_id = uid()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        # Use route templates, never query strings, file names, tokens or request bodies.
        route = request.scope.get("route")
        log.info(
            "http method=%s route=%s status=%s request_id=%s",
            request.method,
            getattr(route, "path", "unmatched"),
            response.status_code,
            request_id,
        )
        return response

    @app.exception_handler(IntegrityError)
    async def conflict(request, exc):
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=409, content={"detail": "Конфликт данных или повторная операция"})

    def audit(session, actor, action, resource_id):
        session.add(AuditEvent(actor_id=actor.id, action=action, resource_id=resource_id))

    def demo_only(actor):
        if not runtime.settings.enable_demo or runtime.settings.environment == "production":
            raise HTTPException(404)
        access.require(actor.manage_access)

    def document_editor(actor, branch_codes):
        access.require(
            bool(set(actor.roles) & {"hr", "hr_head"})
            and bool(branch_codes)
            and all(access.in_scope(actor, b) for b in branch_codes)
        )

    def ticket_for(session, actor, ticket_id, write=False):
        ticket = session.get(Ticket, ticket_id)
        if not ticket:
            raise HTTPException(404, "Обращение не найдено")
        employee = session.scalar(select(Employee).where(Employee.id == ticket.employee_id).with_for_update())
        from . import support

        allowed = (
            support.can_write(session, actor, ticket) if write else support.can_read(session, actor, ticket)
        )
        access.require(allowed)
        return ticket, employee

    @app.get("/health/live", tags=["Operations"])
    def live():
        return {"status": "ok"}

    @app.get("/health/ready", tags=["Operations"])
    def ready(session: Session):
        session.execute(text("SELECT 1"))
        session.scalar(select(Identity.id).limit(1))
        return {
            "status": "ready",
            "ml_mode": runtime.settings.ml_mode,
            "transport": runtime.settings.transport,
        }

    @app.get("/api/me", tags=["Access"])
    def me(actor: Actor):
        return {
            "id": actor.id,
            "name": actor.name,
            "roles": actor.roles,
            "manage_access": actor.manage_access,
            "branch_codes": actor.branch_codes,
            "is_admin": access.is_admin(actor),
            "can_use_workspace": access.can_use_workspace(actor),
            "features": {"optional": runtime.settings.enable_optional, "management": runtime.settings.enable_management},
            "capabilities": {
                "manage_hr": access.is_hr(actor),
                "manage_people": access.can_manage_people(actor),
                "manage_access": actor.manage_access,
                "view_team": "manager" in actor.roles,
                "demo": runtime.settings.enable_demo
                and actor.manage_access
                and runtime.settings.environment != "production",
            },
        }

    @app.get("/api/menu", tags=["Navigation"])
    def action_menu(session: Session, actor: Actor):
        from .menu import available

        employee = session.scalar(select(Employee).where(Employee.identity_id == actor.id))
        return [{"id": a.id, "label": a.label, "description": a.description}
                for a in available(runtime, actor, employee)]

    @app.post("/api/menu/{action_id}", tags=["Navigation"])
    def menu_action(action_id: str, session: Session, actor: Actor):
        from . import menu

        employee = session.scalar(select(Employee).where(Employee.identity_id == actor.id))
        access.require(actor.max_user_id and any(a.id == action_id for a in menu.available(runtime, actor, employee)))
        ingest(session, {"event_id": "web-menu:" + uid(), "max_user_id": actor.max_user_id,
                         "kind": "callback", "payload": "nav:do:" + action_id, "text": ""})
        return {"queued": True}

    @app.get("/api/identities", tags=["Access"])
    def identities(session: Session, actor: Actor):
        access.require(access.can_manage_people(actor))
        return [
            {
                "id": i.id,
                "name": i.name,
                "roles": i.roles,
                "active": i.active,
                "manage_access": i.manage_access,
                "branch_codes": i.branch_codes,
                "max_bound": bool(i.max_user_id),
                "contact_url": i.contact_url,
            }
            for i in session.scalars(select(Identity))
            if access.can_manage_identity(actor, i)
        ]

    @app.post("/api/identities", status_code=201, tags=["Access"])
    def create_identity(data: IdentityCreate, session: Session, actor: Actor):
        access.require(access.can_manage_people(actor))
        if not actor.manage_access:
            access.require(not data.manage_access and access.scope_contains(actor, data.branch_codes))
        token = access.new_token()
        identity = Identity(**data.model_dump(), token_hash=access.token_hash(token))
        session.add(identity)
        session.flush()
        audit(session, actor, "identity.create", identity.id)
        return {"id": identity.id, "api_token": token}

    @app.put("/api/identities/{identity_id}", tags=["Access"])
    def change_identity(identity_id: str, data: IdentityUpdate, session: Session, actor: Actor):
        access.require(access.can_manage_people(actor) and identity_id != "bootstrap")
        identity = session.get(Identity, identity_id)
        if not identity:
            raise HTTPException(404)
        access.require(access.can_manage_identity(actor, identity))
        if not actor.manage_access:
            access.require(not data.manage_access and access.scope_contains(actor, data.branch_codes))
        employee = session.scalar(select(Employee).where(Employee.identity_id == identity_id))
        if employee and "*" not in data.branch_codes and employee.branch_code not in data.branch_codes:
            raise HTTPException(422, "Площадку сотрудника меняйте в его анкете")
        if "contact_url" in data.model_fields_set:
            identity.contact_url = data.contact_url
        for key, value in data.model_dump(exclude_none=True).items():
            setattr(identity, key, value)
        employee = session.scalar(select(Employee).where(Employee.identity_id == identity_id))
        if employee:
            if data.name is not None:
                employee.name = data.name
            employee.revision += 1
            if not identity.active:
                from .admin_service import deactivate_employee

                deactivate_employee(session, employee)
        audit(session, actor, "identity.update", identity.id)
        return {"id": identity.id, "active": identity.active}

    @app.post("/api/identities/{identity_id}/rotate-key", tags=["Access"])
    def rotate_key(identity_id: str, session: Session, actor: Actor):
        access.require(access.can_manage_people(actor) and identity_id != "bootstrap")
        identity = session.get(Identity, identity_id)
        if not identity:
            raise HTTPException(404)
        access.require(access.can_manage_identity(actor, identity))
        token = access.new_token()
        identity.token_hash = access.token_hash(token)
        session.execute(delete(WebSession).where(WebSession.identity_id == identity.id))
        audit(session, actor, "identity.rotate_key", identity.id)
        return {"api_token": token}

    @app.post("/api/identities/{identity_id}/unbind", tags=["Access"])
    def unbind(identity_id: str, session: Session, actor: Actor):
        access.require(access.can_manage_people(actor))
        identity = session.get(Identity, identity_id)
        if not identity:
            raise HTTPException(404)
        access.require(access.can_manage_identity(actor, identity))
        identity.max_user_id = None
        session.execute(delete(WebSession).where(WebSession.identity_id == identity.id))
        employee = session.scalar(select(Employee).where(Employee.identity_id == identity_id))
        if employee:
            employee.status = "invited"
        audit(session, actor, "identity.unbind", identity.id)
        return {"ok": True}

    @app.post("/api/identities/{identity_id}/invitation", tags=["Access"])
    def invitation(identity_id: str, session: Session, actor: Actor):
        employee = session.scalar(select(Employee).where(Employee.identity_id == identity_id))
        identity = session.get(Identity, identity_id)
        access.require(
            bool(
                identity
                and (
                    access.can_manage_identity(actor, identity)
                    or employee
                    and access.can_support(actor, employee)
                )
            )
        )
        token = employees.invite(session, identity_id, runtime.settings.invitation_hours)
        audit(session, actor, "invitation.create", identity_id)
        url = (
            f"https://max.ru/{runtime.settings.max_bot_name}?start={token}"
            if runtime.settings.max_bot_name
            else None
        )
        return {
            "invitation_token": token,
            "max_link": url,
            "expires_in_hours": runtime.settings.invitation_hours,
        }

    @app.get("/api/branches", tags=["Branches"])
    def branches(session: Session, actor: Actor):
        return [
            {
                "code": b.code,
                "name": b.name,
                "timezone": b.timezone,
                "workdays": b.workdays,
                "holidays": b.holidays,
                "working_dates": b.working_dates,
                "resources": b.resources,
                "scenario_id": b.scenario_id,
                "active": b.active,
            }
            for b in session.scalars(select(Branch))
            if access.in_scope(actor, b.code)
        ]

    @app.put("/api/branches/{code}", tags=["Branches"])
    def save_branch(code: str, data: BranchInput, session: Session, actor: Actor):
        access.require(access.can_configure(actor, code))
        if code != data.code:
            raise HTTPException(422, "Код в URL и теле должен совпадать")
        if data.scenario_id:
            scenario = session.get(Scenario, data.scenario_id)
            if not scenario or not ("*" in scenario.branch_codes or code in scenario.branch_codes):
                raise HTTPException(422, "Сценарий недоступен площадке")
        branch = session.get(Branch, code)
        if not branch:
            branch = Branch(code=code)
            session.add(branch)
        else:
            branch.revision += 1
        for key, value in data.model_dump(mode="json").items():
            setattr(branch, key, value)
        session.flush()
        count = 0
        for employee in session.scalars(select(Employee).where(Employee.branch_code == code)):
            count += scenarios.rebuild_schedule(session, employee)
        audit(session, actor, "branch.save", code)
        return {"code": code, "planned_steps": count}

    @app.get("/api/scenarios", tags=["Scenarios"])
    def list_scenarios(session: Session, actor: Actor):
        access.require(bool(set(actor.roles) & {"hr", "hr_head"}))
        return [
            {"id": s.id, "name": s.name, "version": s.version, "definition": s.definition}
            for s in session.scalars(
                select(Scenario).where(Scenario.kind == "onboarding", Scenario.active.is_(True))
            )
            if "*" in s.branch_codes or any(access.in_scope(actor, b) for b in s.branch_codes)
        ]

    @app.post("/api/scenarios", status_code=201, tags=["Scenarios"])
    def upload_scenario(data: ScenarioDefinition, session: Session, actor: Actor):
        access.require(bool(set(actor.roles) & {"hr", "hr_head"}) and bool(actor.branch_codes))
        for step in data.steps:
            for doc_id in step.document_ids:
                doc = session.get(Document, doc_id)
                access.require(bool(doc and documents.can_read(session, actor, doc)), "Недоступное вложение")
        scenario = Scenario(
            name=data.name,
            version=data.version,
            definition=data.model_dump(mode="json"),
            branch_codes=actor.branch_codes,
        )
        session.add(scenario)
        session.flush()
        audit(session, actor, "scenario.create", scenario.id)
        return {"id": scenario.id, "name": scenario.name, "version": scenario.version}

    @app.post("/api/employees", status_code=201, tags=["Employees"])
    def create_employee(data: EmployeeCreate, session: Session, actor: Actor):
        employee = employees.create_employee(session, actor, data)
        scenarios.rebuild_schedule(session, employee)
        audit(session, actor, "employee.create", employee.id)
        return employees.employee_summary(employee)

    @app.get("/api/staff-directory", tags=["Employees"])
    def staff_directory(session: Session, actor: Actor):
        access.require(bool(set(actor.roles) & {"hr", "hr_head"}))
        return [
            {"id": i.id, "name": i.name, "roles": i.roles, "branch_codes": i.branch_codes}
            for i in session.scalars(select(Identity).where(Identity.active.is_(True)))
            if set(i.roles) & {"hr", "manager", "hr_head"}
            and (
                "*" in actor.branch_codes
                or "*" in i.branch_codes
                or set(actor.branch_codes) & set(i.branch_codes)
            )
        ]

    @app.get("/api/employees", tags=["Employees"])
    def list_employees(session: Session, actor: Actor):
        return [employees.employee_summary(e) for e in analytics.visible_employees(session, actor)]

    @app.get("/api/employees/{employee_id}", tags=["Employees"])
    def get_employee(employee_id: str, session: Session, actor: Actor):
        return employees.employee_summary(access.employee_for(session, actor, employee_id))

    @app.patch("/api/employees/{employee_id}/dates", tags=["Employees"])
    def change_dates(employee_id: str, data: EmployeeDates, session: Session, actor: Actor):
        employee = access.employee_for(session, actor, employee_id, support=True)
        end = data.probation_end or add_months(data.hire_date, 3)
        if end < data.hire_date:
            raise HTTPException(422, "Окончание раньше выхода")
        employee.hire_date, employee.probation_end = data.hire_date, end
        employee.revision += 1
        count = scenarios.rebuild_schedule(session, employee)
        audit(session, actor, "employee.reschedule", employee.id)
        return {"employee": employees.employee_summary(employee), "planned_steps": count}

    @app.post("/api/employees/{employee_id}/deactivate", tags=["Employees"])
    def deactivate(employee_id: str, session: Session, actor: Actor):
        employee = access.employee_for(session, actor, employee_id, support=True)
        from .admin_service import deactivate_employee

        deactivate_employee(session, employee)
        employee.revision += 1
        audit(session, actor, "employee.deactivate", employee.id)
        return {"ok": True}

    @app.get("/api/employees/{employee_id}/schedule", tags=["Scenarios"])
    def schedule(employee_id: str, session: Session, actor: Actor):
        access.employee_for(session, actor, employee_id)
        return [
            scenarios.step_public(s)
            for s in session.scalars(
                select(ScheduledStep)
                .where(ScheduledStep.employee_id == employee_id)
                .order_by(ScheduledStep.due_at)
            )
        ]

    @app.post("/api/steps/{step_id}/trigger", tags=["Scenarios"])
    def trigger(step_id: str, session: Session, actor: Actor):
        from .queue import enqueue

        step = session.get(ScheduledStep, step_id)
        if not step:
            raise HTTPException(404)
        access.employee_for(session, actor, step.employee_id, support=True)
        from .onboarding import available

        if (
            not step.campaign_id
            and not available(session, session.get(Employee, step.employee_id))
            and step.status != "late_review"
        ):
            raise HTTPException(409, "Адаптация сотрудника завершена или остановлена")
        if step.status not in {"manual", "late_review"}:
            raise HTTPException(409, "Шаг уже запущен или не требует ручного запуска")
        step.status = "enqueued"
        enqueue(session, "step", {"step_id": step.id}, f"step:{step.id}", now(session))
        audit(session, actor, "step.trigger", step.id)
        return {"status": step.status}

    @app.get("/api/employees/{employee_id}/surveys", tags=["Surveys"])
    def survey_progress(employee_id: str, session: Session, actor: Actor):
        employee = access.employee_for(session, actor, employee_id)
        raw_allowed = employee.identity_id == actor.id or access.can_support(actor, employee)
        result = []
        for run in session.scalars(select(SurveyRun).where(SurveyRun.employee_id == employee_id)):
            entry = {
                "id": run.id,
                "title": run.definition["title"],
                "status": run.status,
                "position": run.position,
                "questions": len(run.definition["questions"]),
                "expires_at": run.expires_at,
            }
            if raw_allowed:
                entry["result"] = run.result
                entry["answers"] = [
                    {"id": a.id, "question_id": a.question_id, "value": a.value, "comment": a.comment}
                    for a in session.scalars(select(SurveyAnswer).where(SurveyAnswer.run_id == run.id))
                ]
                entry["awaiting_comment"] = run.awaiting_comment
            result.append(entry)
        return result

    @app.get("/api/tickets", tags=["Tickets"])
    def list_tickets(session: Session, actor: Actor):
        result = []
        for ticket in session.scalars(select(Ticket).order_by(Ticket.created_at.desc())):
            employee = session.get(Employee, ticket.employee_id)
            if access.can_support(actor, employee) or employee.identity_id == actor.id:
                result.append(
                    {
                        "id": ticket.id,
                        "employee_id": employee.id,
                        "reason": ticket.reason,
                        "status": ticket.status,
                        "human_mode": ticket.human_mode,
                        "recruiter_id": ticket.recruiter_id,
                        "created_at": ticket.created_at,
                    }
                )
        return result

    @app.get("/api/tickets/{ticket_id}", tags=["Tickets"])
    def get_ticket(ticket_id: str, session: Session, actor: Actor):
        ticket, employee = ticket_for(session, actor, ticket_id)
        from .support import can_write

        return {
            "id": ticket.id,
            "employee_id": employee.id,
            "status": ticket.status,
            "reason": ticket.reason,
            "can_reply": can_write(session, actor, ticket),
            "resolution": ticket.resolution,
            "outcome": ticket.outcome,
            "context": tickets.ticket_context(session, ticket),
            "human_mode": ticket.human_mode,
            "messages": [
                {"id": m.id, "author_id": m.author_id, "text": m.text, "at": m.created_at}
                for m in session.scalars(
                    select(TicketMessage)
                    .where(TicketMessage.ticket_id == ticket_id)
                    .order_by(TicketMessage.created_at)
                )
            ],
        }

    @app.post("/api/tickets/{ticket_id}/reply", tags=["Tickets"])
    def reply_ticket(
        ticket_id: str,
        data: TextInput,
        session: Session,
        actor: Actor,
        idempotency_key: Annotated[str, Header(min_length=1, max_length=120)],
    ):
        ticket, employee = ticket_for(session, actor, ticket_id, write=True)
        key = f"api:{actor.id}:{ticket.id}:{idempotency_key}"
        if not session.scalar(select(Outbox.id).where(Outbox.dedupe_key == "hr-reply:" + key)):
            tickets.reply(session, actor, employee, ticket, data.text, key)
            audit(session, actor, "ticket.reply", ticket.id)
        return {"status": ticket.status}

    @app.post("/api/tickets/{ticket_id}/close", tags=["Tickets"])
    def close_ticket(ticket_id: str, session: Session, actor: Actor):
        ticket, employee = ticket_for(session, actor, ticket_id, write=True)
        tickets.close(session, actor, employee, ticket, ticket.id)
        audit(session, actor, "ticket.close", ticket.id)
        return {"status": ticket.status}

    @app.get("/api/employees/{employee_id}/analyses", tags=["ML"])
    def analyses(employee_id: str, session: Session, actor: Actor):
        access.employee_for(session, actor, employee_id, support=True)
        return [
            {
                "id": a.id,
                "answer_id": a.answer_id,
                "result": a.result,
                "processor_version": a.processor_version,
            }
            for a in session.scalars(
                select(FeedbackAnalysis).where(FeedbackAnalysis.employee_id == employee_id)
            )
        ]

    @app.post("/api/documents", status_code=201, tags=["Documents"])
    def upload_document(
        session: Session,
        actor: Actor,
        file: Annotated[UploadFile, File()],
        title: Annotated[str, Form(min_length=1, max_length=200)],
        branch_codes: Annotated[str, Form()],
        categories: Annotated[str, Form()] = "[]",
        version: Annotated[int, Form(ge=1)] = 1,
        auto_publish: Annotated[str, Form(pattern="^(true|false)$")] = "true",
    ):
        try:
            codes, cats = json.loads(branch_codes), json.loads(categories)
            if (
                not isinstance(codes, list)
                or not isinstance(cats, list)
                or not all(isinstance(x, str) for x in codes + cats)
            ):
                raise ValueError()
        except ValueError:
            raise HTTPException(422, "branch_codes и categories должны быть JSON-массивами строк") from None
        document_editor(actor, codes)
        for code in codes:
            if code != "*" and not session.get(Branch, code):
                raise HTTPException(422, "Неизвестная площадка")
        content = file.file.read(runtime.settings.max_upload_bytes + 1)
        doc = documents.store(
            session,
            runtime.settings,
            title,
            file.filename or "file",
            content,
            codes,
            cats,
            version,
            auto_publish=auto_publish == "true",
        )
        audit(session, actor, "document.upload", doc.id)
        return documents.public_document(doc)

    @app.get("/api/documents", tags=["Documents"])
    def list_documents(session: Session, actor: Actor):
        return [
            documents.public_document(d)
            for d in session.scalars(select(Document))
            if d.purpose == "knowledge" and documents.can_read(session, actor, d)
        ]

    @app.get("/api/documents/{document_id}/download", tags=["Documents"])
    def download_document(document_id: str, session: Session, actor: Actor):
        doc = session.get(Document, document_id)
        if not doc:
            raise HTTPException(404)
        access.require(documents.can_read(session, actor, doc))
        return FileResponse(
            runtime.settings.data_dir / "documents" / doc.storage_name,
            filename=doc.original_name,
            media_type="application/octet-stream",
        )

    @app.post("/api/documents/{document_id}/download-link", tags=["Documents"])
    def download_link(document_id: str, session: Session, actor: Actor):
        from urllib.parse import urlencode, urljoin

        doc = session.get(Document, document_id)
        if not doc:
            raise HTTPException(404)
        access.require(documents.can_read(session, actor, doc))
        if not runtime.settings.webapp_public_url:
            raise HTTPException(409, "Для скачивания внутри MAX настройте публичный HTTPS-адрес")
        token = download_links.issue(runtime.settings.bootstrap_token, actor.id, doc.id)
        path = f"/api/documents/{doc.id}/native-download?" + urlencode({"ticket": token})
        return {"url": urljoin(runtime.settings.webapp_public_url, path), "expires_in": 60}

    @app.get("/api/documents/{document_id}/native-download", tags=["Documents"])
    def native_download(document_id: str, ticket: str, session: Session):
        identity_id = download_links.validate(runtime.settings.bootstrap_token, ticket, document_id)
        actor = session.get(Identity, identity_id)
        doc = session.get(Document, document_id)
        access.require(bool(actor and actor.active and doc and documents.can_read(session, actor, doc)))
        return FileResponse(
            runtime.settings.data_dir / "documents" / doc.storage_name,
            filename=doc.original_name,
            media_type="application/octet-stream",
            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
        )

    @app.post("/api/documents/{document_id}/deactivate", tags=["Documents"])
    def deactivate_document(document_id: str, session: Session, actor: Actor):
        doc = session.get(Document, document_id)
        if not doc:
            raise HTTPException(404)
        access.require(documents.can_edit(actor, doc))
        doc.active = False
        doc.revision += 1
        audit(session, actor, "document.deactivate", doc.id)
        return {"ok": True}

    @app.post("/api/documents/{document_id}/reindex", tags=["Documents"])
    def reindex(document_id: str, session: Session, actor: Actor):
        from .queue import enqueue

        doc = session.get(Document, document_id)
        if not doc:
            raise HTTPException(404)
        access.require(doc.active and documents.can_edit(actor, doc))
        if doc.purpose != "knowledge":
            raise HTTPException(422, "Персональные планы и вложения не индексируются в базе знаний")
        doc.status = "pending"
        doc.last_error = None
        enqueue(session, "index_document", {"document_id": doc.id}, f"reindex:{doc.id}:{uid()}", now(session))
        audit(session, actor, "document.reindex", doc.id)
        return {"status": doc.status}

    @app.get("/api/dashboard", tags=["Analytics"])
    def dashboard(session: Session, actor: Actor, start: datetime | None = None, end: datetime | None = None):
        if (
            (start and start.tzinfo is None)
            or (end and end.tzinfo is None)
            or (start and end and start >= end)
        ):
            raise HTTPException(422, "Укажите интервал с часовым поясом: start < end")
        return analytics.dashboard(session, actor, start, end)

    @app.put("/api/policies/escalation", tags=["Configuration"])
    def save_escalation(data: EscalationRules, session: Session, actor: Actor):
        access.require(access.is_hr(actor) and "*" in actor.branch_codes)
        session.merge(State(key="escalation_rules", value=data.model_dump()))
        audit(session, actor, "policy.escalation", "global")
        return data

    @app.put("/api/policies/digest", tags=["Configuration"])
    def save_digest(data: DigestPolicy, session: Session, actor: Actor):
        access.require(access.is_hr(actor) and "*" in actor.branch_codes)
        session.merge(State(key="digest_policy", value=data.model_dump()))
        audit(session, actor, "policy.digest", "global")
        return data

    @app.get("/api/operations", tags=["Operations"])
    def operations(session: Session, actor: Actor):
        access.require(actor.manage_access)
        heartbeat = session.get(State, "worker_heartbeat")
        return {
            "jobs": dict(session.execute(select(Job.status, func.count()).group_by(Job.status)).all()),
            "outbox": dict(
                session.execute(select(Outbox.status, func.count()).group_by(Outbox.status)).all()
            ),
            "worker_heartbeat": heartbeat.value if heartbeat else None,
        }

    @app.get("/api/audit", tags=["Operations"])
    def audit_log(session: Session, actor: Actor, limit: int = Query(default=100, ge=1, le=1000)):
        access.require(actor.manage_access)
        return [
            {"actor_id": a.actor_id, "action": a.action, "resource_id": a.resource_id, "at": a.created_at}
            for a in session.scalars(select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(limit))
        ]

    @app.get("/api/operations/jobs", tags=["Operations"])
    def operation_jobs(session: Session, actor: Actor):
        access.require(actor.manage_access)
        return [
            {
                "id": j.id,
                "kind": j.kind,
                "status": j.status,
                "attempts": j.attempts,
                "last_error": j.last_error,
            }
            for j in session.scalars(
                select(Job).where(Job.status.in_(["retry", "failed"])).order_by(Job.created_at).limit(500)
            )
        ]

    @app.post("/api/operations/jobs/{job_id}/retry", tags=["Operations"])
    def retry_job(job_id: str, session: Session, actor: Actor):
        access.require(actor.manage_access)
        job = session.get(Job, job_id)
        if not job:
            raise HTTPException(404)
        if job.status != "failed":
            raise HTTPException(409, "Повтор доступен только для failed-задания")
        job.status, job.attempts, job.due_at = "retry", 0, now(session)
        audit(session, actor, "job.retry", job.id)
        return {"status": job.status}

    @app.get("/api/operations/deliveries", tags=["Operations"])
    def delivery_problems(session: Session, actor: Actor):
        access.require(actor.manage_access)
        return [
            {"id": o.id, "status": o.status, "attempts": o.attempts, "last_error": o.last_error}
            for o in session.scalars(
                select(Outbox)
                .where(Outbox.status.in_(["failed", "uncertain"]))
                .order_by(Outbox.created_at)
                .limit(500)
            )
        ]

    from .schemas import DeliveryResolution

    @app.post("/api/operations/deliveries/{delivery_id}/resolve", tags=["Operations"])
    def resolve_delivery(delivery_id: str, data: DeliveryResolution, session: Session, actor: Actor):
        access.require(actor.manage_access)
        delivery = session.get(Outbox, delivery_id)
        if not delivery:
            raise HTTPException(404)
        if delivery.status not in {"failed", "uncertain"}:
            raise HTTPException(409, "Отправка не требует решения")
        if data.action == "mark_sent":
            delivery.status = "sent"
        else:
            if delivery.status == "uncertain" and not data.accept_possible_duplicate:
                raise HTTPException(422, "Подтвердите риск повтора: accept_possible_duplicate=true")
            delivery.status, delivery.attempts, delivery.retry_at = "retry", 0, now(session)
        audit(session, actor, "delivery." + data.action, delivery.id)
        return {"status": delivery.status}

    @app.post("/webhooks/max", tags=["MAX"])
    def webhook(update: dict, session: Session, x_max_bot_api_secret: Annotated[str | None, Header()] = None):
        secret = runtime.settings.max_webhook_secret
        if not secret or not secrets.compare_digest(x_max_bot_api_secret or "", secret):
            raise HTTPException(403)
        event = normalize_update(update)
        if event:
            ingest(session, event)
        return {"ok": True}

    @app.post("/api/demo/events", status_code=202, tags=["Demo"])
    def simulate(data: DemoEvent, session: Session, actor: Actor):
        demo_only(actor)
        job = ingest(session, {**data.model_dump(), "event_id": "demo:" + data.event_id})
        return {"job_id": job.id, "status": job.status}

    @app.put("/api/demo/clock", tags=["Demo"])
    def set_clock(data: ClockInput, session: Session, actor: Actor):
        demo_only(actor)
        session.merge(State(key="clock", value={"now": data.now.isoformat() if data.now else None}))
        audit(session, actor, "demo.clock", "clock")
        return {"now": data.now}

    @app.post("/api/demo/tick", tags=["Demo"])
    def tick(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
        limit: int = Query(default=100, ge=1, le=2000),
    ):
        if not credentials:
            raise HTTPException(401)
        with runtime.db.transaction() as session:
            actor = access.authenticate(session, credentials.credentials, runtime.settings.bootstrap_token)
            demo_only(actor)
        return Worker(runtime).tick(limit)

    @app.get("/api/demo/outbox", tags=["Demo"])
    def demo_outbox(session: Session, actor: Actor, limit: int = Query(default=100, ge=1, le=1000)):
        demo_only(actor)
        return [
            {
                "id": o.id,
                "recipient_id": o.recipient_id,
                "target_user_id": o.target_user_id,
                "body": o.body,
                "status": o.status,
                "last_error": o.last_error,
            }
            for o in session.scalars(
                select(Outbox).order_by(Outbox.created_at.desc(), Outbox.id).limit(limit)
            )
        ]

    @app.get("/app", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
    def dashboard_page():
        return HTMLResponse(
            (Path(__file__).parent / "web" / "app.html").read_text(encoding="utf-8"),
            headers={
                "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
            },
        )

    return app
