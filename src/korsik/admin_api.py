"""Administrative HTTP surface shared by browser and authenticated MAX Mini App."""

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import func, select

from . import access, analytics, campaigns, documents, employees, scenarios, tickets, webapp
from . import admin_service as service
from .admin_schemas import (
    AudienceInput,
    BranchEdit,
    CampaignInput,
    DepartmentInput,
    DocumentInput,
    DocumentPublication,
    EmployeeInput,
    LocalLoginInput,
    ManagerRuleInput,
    ManualTicketInput,
    MaxLaunchInput,
    OnboardingAction,
    PublishInput,
    RevisionInput,
    ScenarioEdit,
    SurveyInput,
    TicketInput,
)
from .dependencies import Actor, Admin, Session, bearer, runtime_dependency
from .feedback import processing_status
from .models import (
    AssignmentHistory,
    Branch,
    Campaign,
    Department,
    Document,
    Employee,
    FeedbackAnalysis,
    Identity,
    ManagerRule,
    Scenario,
    ScheduledStep,
    State,
    SurveyAnswer,
    SurveyRun,
    SurveyTemplate,
    Ticket,
    WebSession,
    uid,
)

router = APIRouter(prefix="/api/admin", tags=["HR Workspace"])


@router.get("/policies")
def policies(session: Session, actor: Admin):
    from .schemas import DigestPolicy, EscalationRules

    service.require_hr(actor)
    result = {"can_edit": "*" in actor.branch_codes}
    for name, model in [("digest_policy", DigestPolicy), ("escalation_rules", EscalationRules)]:
        state = session.get(State, name)
        result[name] = model.model_validate(state.value if state else {}).model_dump()
    return result


def resource(session, model, resource_id):
    entity = session.get(model, resource_id)
    if not entity:
        raise HTTPException(404, "Запись не найдена")
    return entity


def scoped_resource(session, actor, model, resource_id):
    entity = resource(session, model, resource_id)
    service.check_scope(
        actor, [entity.branch_code] if hasattr(entity, "branch_code") else entity.branch_codes
    )
    return entity


@router.get("/config")
def config(runtime=Depends(runtime_dependency)):
    settings = runtime.settings
    return {
        "local_login": settings.environment != "production" and settings.allow_local_admin_login,
        "max_configured": bool(settings.webapp_public_url),
        "development": settings.environment != "production",
    }


@router.post("/auth/max")
def max_login(data: MaxLaunchInput, session: Session, runtime=Depends(runtime_dependency)):
    return webapp.launch_session(session, runtime.settings, data.init_data)


@router.post("/auth/local")
def local_login(data: LocalLoginInput, session: Session, runtime=Depends(runtime_dependency)):
    if runtime.settings.environment == "production" or not runtime.settings.allow_local_admin_login:
        raise HTTPException(403, "Вход доступен через мини-приложение MAX")
    actor = access.authenticate(session, data.api_key, runtime.settings.bootstrap_token)
    return webapp.issue_session(session, actor, "local")


@router.post("/auth/logout")
def logout(session: Session, actor: Actor, credentials=Depends(bearer)):
    if credentials:
        existing = session.get(WebSession, access.token_hash(credentials.credentials))
        if existing:
            session.delete(existing)
    return {"ok": True}


@router.get("/overview")
def overview(session: Session, actor: Admin, runtime=Depends(runtime_dependency)):
    if not access.is_hr(actor):
        return {"access_only": True, "totals": {}, "environment": runtime.settings.environment}
    data = analytics.dashboard(session, actor)
    data["document_count"] = sum(
        d.active
        and d.published
        and d.status in {"ready", "ready_stub"}
        and documents.can_read(session, actor, d)
        for d in session.scalars(select(Document))
    )
    data["branch_count"] = sum(
        b.active and access.in_scope(actor, b.code) for b in session.scalars(select(Branch))
    )
    data["recent_tickets"] = ticket_list(session, actor, "open")[:6]
    data["ml_mode"] = runtime.settings.ml_mode
    data["transport"] = runtime.settings.transport
    return data


@router.get("/employees")
def employee_list(session: Session, actor: Admin, search: str = "", branch: str = "", archived: bool = False):
    service.require_hr(actor)
    rows = [e for e in analytics.visible_employees(session, actor) if e.active != archived]
    if branch:
        rows = [e for e in rows if e.branch_code == branch]
    if search:
        term = search.casefold()
        rows = [e for e in rows if term in (e.name + " " + e.department).casefold()]
    return [service.employee_dto(session, e) for e in rows]


@router.post("/employees", status_code=201)
def create_employee(data: EmployeeInput, session: Session, actor: Admin):
    return service.employee_dto(session, service.save_employee(session, actor, data))


@router.get("/employees/{employee_id}")
def employee_detail(employee_id: str, session: Session, actor: Admin):
    employee = access.employee_for(session, actor, employee_id, support=True)
    result = service.employee_dto(session, employee)
    from .models import RouteRun

    result["route_runs"] = [
        {
            "id": r.id,
            "scheduled_step_id": r.scheduled_step_id,
            "position": r.position,
            "status": r.status,
            "last_error": r.last_error,
            "trace": r.trace,
        }
        for r in session.scalars(select(RouteRun).where(RouteRun.employee_id == employee.id))
    ]
    result["schedule"] = [
        {**scenarios.step_public(s), "text": s.payload["text"], "campaign_id": s.campaign_id}
        for s in session.scalars(
            select(ScheduledStep)
            .where(ScheduledStep.employee_id == employee.id)
            .order_by(ScheduledStep.due_at)
        )
    ]
    result["surveys"] = []
    for run in session.scalars(
        select(SurveyRun).where(SurveyRun.employee_id == employee.id).order_by(SurveyRun.started_at.desc())
    ):
        answers = []
        for answer in session.scalars(select(SurveyAnswer).where(SurveyAnswer.run_id == run.id)):
            analysis = session.scalar(select(FeedbackAnalysis).where(FeedbackAnalysis.answer_id == answer.id))
            answers.append(
                {
                    "question_id": answer.question_id,
                    "value": answer.value,
                    "comment": answer.comment,
                    "analysis": analysis.result if analysis else None,
                    "analysis_processing": processing_status(session, answer),
                }
            )
        result["surveys"].append(
            {
                "id": run.id,
                "definition": run.definition,
                "status": run.status,
                "position": run.position,
                "answers": answers,
                "expires_at": run.expires_at,
                "result": run.result,
            }
        )
    result["assignment_history"] = [
        {"previous": h.previous, "current": h.current, "created_at": h.created_at}
        for h in session.scalars(
            select(AssignmentHistory)
            .where(AssignmentHistory.employee_id == employee.id)
            .order_by(AssignmentHistory.created_at)
        )
    ]
    return result


@router.post("/employees/{employee_id}/onboarding")
def employee_onboarding(employee_id: str, data: OnboardingAction, session: Session, actor: Admin):
    from . import onboarding

    employee = access.employee_for(session, actor, employee_id, support=True)
    if data.action == "resume":
        if onboarding.past_end(session, employee):
            raise HTTPException(409, "Сначала установите будущую дату окончания адаптации")
        employee.onboarding_status, employee.onboarding_ended_at = "active", None
        scenarios.rebuild_schedule(session, employee)
    else:
        onboarding.finish(session, employee, "completed" if data.action == "complete" else "stopped")
    employee.revision += 1
    service.audit(session, actor, "onboarding." + data.action, employee.id)
    return service.employee_dto(session, employee)


@router.get("/manager-rules")
def manager_rules(session: Session, actor: Admin):
    service.require_hr(actor)
    return [
        {"id": r.id, "recruiter_id": r.recruiter_id, "branch_code": r.branch_code, "manager_id": r.manager_id}
        for r in session.scalars(select(ManagerRule))
        if access.in_scope(actor, r.branch_code)
    ]


@router.put("/manager-rules")
def save_manager_rule(data: ManagerRuleInput, session: Session, actor: Admin):
    service.check_branches(session, actor, [data.branch_code])
    service.identity_reference(session, data.recruiter_id, ["hr", "hr_head"], data.branch_code)
    service.identity_reference(session, data.manager_id, ["manager"], data.branch_code)
    rule = session.scalar(
        select(ManagerRule).where(
            ManagerRule.recruiter_id == data.recruiter_id, ManagerRule.branch_code == data.branch_code
        )
    )
    if not rule:
        rule = ManagerRule(**data.model_dump())
        session.add(rule)
    else:
        rule.manager_id = data.manager_id
    session.flush()
    service.audit(session, actor, "manager_rule.save", rule.id)
    return {"id": rule.id}


@router.delete("/manager-rules/{rule_id}")
def delete_manager_rule(rule_id: str, session: Session, actor: Admin):
    rule = resource(session, ManagerRule, rule_id)
    service.check_scope(actor, [rule.branch_code])
    service.audit(session, actor, "manager_rule.delete", rule.id)
    session.delete(rule)
    return {"ok": True}


@router.put("/manager-rules/{rule_id}")
def update_manager_rule(rule_id: str, data: ManagerRuleInput, session: Session, actor: Admin):
    rule = resource(session, ManagerRule, rule_id)
    service.check_scope(actor, [rule.branch_code])
    service.check_branches(session, actor, [data.branch_code])
    service.identity_reference(session, data.recruiter_id, ["hr", "hr_head"], data.branch_code)
    service.identity_reference(session, data.manager_id, ["manager"], data.branch_code)
    for key, value in data.model_dump().items():
        setattr(rule, key, value)
    service.audit(session, actor, "manager_rule.update", rule.id)
    return {"id": rule.id}


@router.put("/employees/{employee_id}")
def update_employee(employee_id: str, data: EmployeeInput, session: Session, actor: Admin):
    return service.employee_dto(session, service.save_employee(session, actor, data, employee_id))


@router.delete("/employees/{employee_id}")
def archive_employee(employee_id: str, session: Session, actor: Admin):
    employee = access.employee_for(session, actor, employee_id, support=True)
    service.deactivate_employee(session, employee)
    employee.revision += 1
    service.audit(session, actor, "employee.archive", employee.id)
    return {"ok": True}


@router.post("/employees/{employee_id}/restore")
def restore_employee(employee_id: str, session: Session, actor: Admin):
    employee = access.employee_for(session, actor, employee_id, support=True)
    branch = session.get(Branch, employee.branch_code)
    if not branch.active:
        raise HTTPException(409, "Сначала восстановите площадку")
    identity = session.get(Identity, employee.identity_id)
    employee.active, identity.active = True, True
    employee.status = "active" if identity.max_user_id else "invited"
    employee.revision += 1
    scenarios.rebuild_schedule(session, employee)
    service.audit(session, actor, "employee.restore", employee.id)
    return service.employee_dto(session, employee)


@router.post("/employees/{employee_id}/invitation")
def employee_invite(employee_id: str, session: Session, actor: Admin, runtime=Depends(runtime_dependency)):
    employee = access.employee_for(session, actor, employee_id, support=True)
    token = employees.invite(session, employee.identity_id, runtime.settings.invitation_hours)
    name = runtime.settings.max_bot_name.lstrip("@")
    service.audit(session, actor, "invitation.create", employee.identity_id)
    return {
        "token": token,
        "url": f"https://max.ru/{name}?start={token}" if name else None,
        "command": "/start " + token,
        "hours": runtime.settings.invitation_hours,
    }


@router.get("/branches")
def branch_list(session: Session, actor: Admin, archived: bool = False):
    service.require_hr(actor)
    return [
        service.branch_dto(session, b)
        for b in session.scalars(select(Branch).order_by(Branch.name))
        if access.in_scope(actor, b.code) and b.active != archived
    ]


@router.put("/branches/{code}")
def save_branch(code: str, data: BranchEdit, session: Session, actor: Admin):
    return service.branch_dto(session, service.save_branch(session, actor, data, code))


@router.delete("/branches/{code}")
def archive_branch(code: str, session: Session, actor: Admin):
    service.check_scope(actor, [code])
    branch = resource(session, Branch, code)
    count = session.scalar(
        select(func.count())
        .select_from(Employee)
        .where(Employee.branch_code == code, Employee.active.is_(True))
    )
    if count:
        raise HTTPException(
            409, f"На площадке {count} активных сотрудников. Сначала перенесите или архивируйте их"
        )
    branch.active = False
    branch.revision += 1
    service.audit(session, actor, "branch.archive", code)
    return {"ok": True}


@router.post("/branches/{code}/restore")
def restore_branch(code: str, session: Session, actor: Admin):
    service.check_scope(actor, [code])
    branch = resource(session, Branch, code)
    branch.active = True
    branch.revision += 1
    service.audit(session, actor, "branch.restore", code)
    return service.branch_dto(session, branch)


@router.get("/departments")
def department_list(session: Session, actor: Admin, archived: bool = False):
    service.require_hr(actor)
    return [
        service.department_dto(session, d)
        for d in session.scalars(select(Department).order_by(Department.name))
        if access.in_scope(actor, d.branch_code) and d.active != archived
    ]


@router.post("/departments", status_code=201)
def create_department(data: DepartmentInput, session: Session, actor: Admin):
    return service.department_dto(session, service.save_department(session, actor, data))


@router.put("/departments/{department_id}")
def update_department(department_id: str, data: DepartmentInput, session: Session, actor: Admin):
    return service.department_dto(session, service.save_department(session, actor, data, department_id))


@router.delete("/departments/{department_id}")
def archive_department(department_id: str, session: Session, actor: Admin):
    department = scoped_resource(session, actor, Department, department_id)
    count = session.scalar(
        select(func.count())
        .select_from(Employee)
        .where(Employee.department_id == department.id, Employee.active.is_(True))
    )
    if count:
        raise HTTPException(
            409, f"В подразделении {count} активных сотрудников. Сначала измените их подразделение"
        )
    service.archive(session, actor, department)
    return {"ok": True}


@router.post("/departments/{department_id}/restore")
def restore_department(department_id: str, session: Session, actor: Admin):
    department = scoped_resource(session, actor, Department, department_id)
    if not session.get(Branch, department.branch_code).active:
        raise HTTPException(409, "Сначала восстановите площадку")
    service.archive(session, actor, department, active=True)
    return service.department_dto(session, department)


@router.get("/documents")
def document_list(session: Session, actor: Admin, archived: bool = False):
    service.require_hr(actor)
    return [
        {**documents.public_document(d), "revision": d.revision, "created_at": d.created_at}
        for d in session.scalars(select(Document).order_by(Document.created_at.desc()))
        if service.visible_scoped(actor, d.branch_codes) and d.active != archived
    ]


@router.put("/documents/{document_id}")
def update_document(document_id: str, data: DocumentInput, session: Session, actor: Admin):
    document = scoped_resource(session, actor, Document, document_id)
    service.check_branches(session, actor, data.branch_codes)
    service.revision_check(document, data.revision)
    document.title, document.branch_codes, document.categories = (
        data.title,
        data.branch_codes,
        data.categories,
    )
    document.revision += 1
    service.audit(session, actor, "document.update", document.id)
    return {**documents.public_document(document), "revision": document.revision}


@router.delete("/documents/{document_id}")
def archive_document(document_id: str, session: Session, actor: Admin):
    document = scoped_resource(session, actor, Document, document_id)
    service.archive(session, actor, document)
    return {"ok": True}


@router.post("/documents/{document_id}/restore")
def restore_document(document_id: str, session: Session, actor: Admin):
    document = scoped_resource(session, actor, Document, document_id)
    service.archive(session, actor, document, active=True)
    # Restoring a retired version must never silently replace the current regulation.
    document.published = False
    return documents.public_document(document)


@router.post("/documents/{document_id}/version", status_code=201)
def document_version(
    document_id: str,
    session: Session,
    actor: Admin,
    file: UploadFile = File(),
    runtime=Depends(runtime_dependency),
):
    previous = scoped_resource(session, actor, Document, document_id)
    root = previous.family_id or previous.id
    session.scalar(select(Document).where(Document.id == root).with_for_update())
    version = (
        session.scalar(select(func.max(Document.version)).where(Document.family_id == root))
        or previous.version
    )
    content = file.file.read(runtime.settings.max_upload_bytes + 1)
    new = documents.store(
        session,
        runtime.settings,
        previous.title,
        file.filename or "file",
        content,
        previous.branch_codes,
        previous.categories,
        version + 1,
        previous=previous,
        auto_publish=previous.auto_publish,
    )
    service.audit(session, actor, "document.replace", previous.id)
    return documents.public_document(new)


@router.post("/documents/{document_id}/publish")
def publish_document(document_id: str, data: RevisionInput, session: Session, actor: Admin):
    document = scoped_resource(session, actor, Document, document_id)
    for version in session.scalars(
        select(Document).where(Document.family_id == (document.family_id or document.id))
    ):
        service.check_scope(actor, version.branch_codes)
    service.revision_check(document, data.revision)
    documents.publish(session, document)
    service.audit(session, actor, "document.publish", document.id)
    return documents.public_document(document)


@router.put("/documents/{document_id}/publication")
def document_publication(document_id: str, data: DocumentPublication, session: Session, actor: Admin):
    document = scoped_resource(session, actor, Document, document_id)
    service.revision_check(document, data.revision)
    document.auto_publish = data.auto_publish
    document.revision += 1
    service.audit(session, actor, "document.publication", document.id)
    return documents.public_document(document)


@router.get("/surveys")
def survey_list(session: Session, actor: Admin, archived: bool = False):
    service.require_hr(actor)
    return [
        service.survey_dto(t)
        for t in session.scalars(select(SurveyTemplate).order_by(SurveyTemplate.updated_at.desc()))
        if service.visible_scoped(actor, t.branch_codes) and t.active != archived
    ]


@router.post("/surveys", status_code=201)
def create_survey(data: SurveyInput, session: Session, actor: Admin):
    return service.survey_dto(service.save_survey(session, actor, data))


@router.put("/surveys/{template_id}")
def update_survey(template_id: str, data: SurveyInput, session: Session, actor: Admin):
    return service.survey_dto(service.save_survey(session, actor, data, template_id))


@router.post("/surveys/{template_id}/clone", status_code=201)
def clone_survey(template_id: str, session: Session, actor: Admin):
    template = resource(session, SurveyTemplate, template_id)
    if not service.visible_scoped(actor, template.branch_codes):
        raise HTTPException(403)
    data = SurveyInput(
        title=template.title + " (копия)",
        description=template.description,
        branch_codes=actor.branch_codes,
        questions=template.definition["questions"],
        reminder_days=template.definition.get("reminder_days", [1, 2]),
        kind=template.definition.get("kind", "survey"),
        pass_percent=template.definition.get("pass_percent", 70),
    )
    return service.survey_dto(service.save_survey(session, actor, data))


@router.delete("/surveys/{template_id}")
def archive_survey(template_id: str, session: Session, actor: Admin):
    template = scoped_resource(session, actor, SurveyTemplate, template_id)
    service.archive(session, actor, template)
    return {"ok": True}


@router.post("/surveys/{template_id}/restore")
def restore_survey(template_id: str, session: Session, actor: Admin):
    template = scoped_resource(session, actor, SurveyTemplate, template_id)
    service.archive(session, actor, template, active=True)
    return service.survey_dto(template)


@router.get("/scenarios")
def scenario_list(session: Session, actor: Admin, archived: bool = False):
    service.require_hr(actor)
    return [
        {
            "id": s.id,
            "name": s.name,
            "version": s.version,
            "definition": s.definition,
            "branch_codes": s.branch_codes,
            "active": s.active,
        }
        for s in session.scalars(
            select(Scenario)
            .where(Scenario.kind == "onboarding")
            .order_by(Scenario.name, Scenario.version.desc())
        )
        if service.visible_scoped(actor, s.branch_codes) and s.active != archived
    ]


@router.post("/scenarios", status_code=201)
def create_scenario(data: ScenarioEdit, session: Session, actor: Admin):
    service.check_branches(session, actor, data.branch_codes)
    for step in data.steps:
        for doc_id in step.document_ids:
            doc = resource(session, Document, doc_id)
            access.require(doc.active and documents.can_read(session, actor, doc))
    definition = data.model_dump(mode="json", exclude={"branch_codes", "revision"})
    latest = session.scalar(select(func.max(Scenario.version)).where(Scenario.name == data.name))
    definition["version"] = (latest or 0) + 1
    scenario = Scenario(
        name=data.name, version=definition["version"], definition=definition, branch_codes=data.branch_codes
    )
    session.add(scenario)
    session.flush()
    service.audit(session, actor, "scenario.create", scenario.id)
    return {"id": scenario.id, "name": scenario.name, "version": scenario.version}


@router.put("/scenarios/{scenario_id}")
def update_scenario(scenario_id: str, data: ScenarioEdit, session: Session, actor: Admin):
    previous = scoped_resource(session, actor, Scenario, scenario_id)
    if previous.kind != "onboarding":
        raise HTTPException(422)
    latest = session.scalar(select(func.max(Scenario.version)).where(Scenario.name == previous.name))
    if data.revision is not None and data.revision != latest:
        raise HTTPException(409, "Есть более новая версия сценария. Откройте её перед редактированием")
    result = create_scenario(data, session, actor)
    for branch in session.scalars(select(Branch).where(Branch.scenario_id == previous.id)):
        if access.in_scope(actor, branch.code):
            branch.scenario_id = result["id"]
            branch.revision += 1
            for employee in session.scalars(select(Employee).where(Employee.branch_code == branch.code)):
                scenarios.rebuild_schedule(session, employee)
    return result


@router.delete("/scenarios/{scenario_id}")
def archive_scenario(scenario_id: str, session: Session, actor: Admin):
    scenario = scoped_resource(session, actor, Scenario, scenario_id)
    if session.scalar(select(Branch.code).where(Branch.scenario_id == scenario.id, Branch.active.is_(True))):
        raise HTTPException(
            409, "Сценарий назначен площадке. Сначала выберите другую версию в настройках площадки"
        )
    service.archive(session, actor, scenario)
    return {"ok": True}


@router.post("/scenarios/{scenario_id}/restore")
def restore_scenario(scenario_id: str, session: Session, actor: Admin):
    scenario = scoped_resource(session, actor, Scenario, scenario_id)
    service.archive(session, actor, scenario, active=True)
    return {"ok": True}


@router.post("/campaigns/preview")
def audience_preview(data: AudienceInput, session: Session, actor: Admin):
    return campaigns.preview_audience(session, actor, data)


@router.get("/campaigns")
def campaign_list(session: Session, actor: Admin, archived: bool = False):
    service.require_hr(actor)
    return [
        campaigns.campaign_dto(session, c)
        for c in session.scalars(select(Campaign).order_by(Campaign.created_at.desc()))
        if c.active != archived and access.scope_contains(actor, c.branch_codes)
    ]


@router.post("/campaigns", status_code=201)
def create_campaign(data: CampaignInput, session: Session, actor: Admin):
    return campaigns.campaign_dto(session, campaigns.save_campaign(session, actor, data))


@router.get("/campaigns/{campaign_id}")
def campaign_detail(campaign_id: str, session: Session, actor: Admin):
    campaign = campaigns.get_campaign(session, actor, campaign_id)
    result = campaigns.campaign_dto(session, campaign, include_recipients=True)
    result["survey_snapshot"] = campaign.survey_snapshot
    result["answers"] = []
    for row in result["recipients"]:
        if row["survey_run_id"]:
            result["answers"].extend(
                {
                    "employee_id": row["employee_id"],
                    "employee_name": row["name"],
                    "question_id": a.question_id,
                    "value": a.value,
                    "comment": a.comment,
                }
                for a in session.scalars(
                    select(SurveyAnswer).where(SurveyAnswer.run_id == row["survey_run_id"])
                )
            )
    result["question_statistics"] = []
    for question in (campaign.survey_snapshot or {}).get("questions", []):
        values = [a["value"]["answer"] for a in result["answers"] if a["question_id"] == question["id"]]
        numbers = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
        result["question_statistics"].append(
            {
                "question_id": question["id"],
                "answer_count": len(values),
                "average": round(sum(numbers) / len(numbers), 2)
                if numbers and question["kind"] == "rating"
                else None,
            }
        )
    return result


@router.put("/campaigns/{campaign_id}")
def update_campaign(campaign_id: str, data: CampaignInput, session: Session, actor: Admin):
    return campaigns.campaign_dto(session, campaigns.save_campaign(session, actor, data, campaign_id))


@router.post("/campaigns/{campaign_id}/publish")
def publish_campaign(campaign_id: str, data: PublishInput, session: Session, actor: Admin):
    campaign = campaigns.get_campaign(session, actor, campaign_id)
    service.revision_check(campaign, data.revision)
    return campaigns.campaign_dto(session, campaigns.publish(session, actor, campaign, data.audience_hash))


@router.post("/campaigns/{campaign_id}/cancel")
def cancel_campaign(campaign_id: str, session: Session, actor: Admin):
    campaign = campaigns.get_campaign(session, actor, campaign_id)
    return campaigns.campaign_dto(session, campaigns.cancel(session, actor, campaign))


@router.delete("/campaigns/{campaign_id}")
def archive_campaign(campaign_id: str, session: Session, actor: Admin):
    campaign = campaigns.get_campaign(session, actor, campaign_id)
    campaigns.cancel(session, actor, campaign)
    service.archive(session, actor, campaign)
    return {"ok": True}


@router.post("/campaigns/{campaign_id}/restore")
def restore_campaign(campaign_id: str, session: Session, actor: Admin):
    campaign = campaigns.get_campaign(session, actor, campaign_id)
    service.archive(session, actor, campaign, active=True)
    return campaigns.campaign_dto(session, campaign)


def ticket_list(session, actor, status="", archived=False):
    service.require_hr(actor)
    result = []
    for ticket in session.scalars(select(Ticket).order_by(Ticket.created_at.desc())):
        employee = session.get(Employee, ticket.employee_id)
        if not access.can_support(actor, employee) or bool(ticket.context.get("_archived")) != archived:
            continue
        if status == "open" and ticket.status == "closed" or status == "closed" and ticket.status != "closed":
            continue
        result.append(
            {
                "id": ticket.id,
                "employee_id": employee.id,
                "employee_name": employee.name,
                "department": employee.department,
                "branch_code": employee.branch_code,
                "reason": ticket.reason,
                "status": ticket.status,
                "human_mode": ticket.human_mode,
                "recruiter_id": ticket.recruiter_id,
                "recruiter_name": session.get(Identity, ticket.recruiter_id).name,
                "risk": ticket.context.get("analysis", {}).get("risk"),
                "created_at": ticket.created_at,
            }
        )
    return result


@router.get("/tickets")
def list_tickets(session: Session, actor: Admin, status: str = "", archived: bool = False):
    return ticket_list(session, actor, status, archived)


@router.post("/tickets", status_code=201)
def create_ticket(data: ManualTicketInput, session: Session, actor: Admin):
    employee = access.employee_for(session, actor, data.employee_id, support=True)
    ticket = tickets.open_ticket(
        session, employee, data.reason, {"initiated_by_hr": actor.id}, "hr-created:" + uid(), human=True
    )
    if data.message:
        tickets.reply(session, actor, employee, ticket, data.message, uid())
    service.audit(session, actor, "ticket.create", ticket.id)
    return {"id": ticket.id}


@router.put("/tickets/{ticket_id}")
def update_ticket(ticket_id: str, data: TicketInput, session: Session, actor: Admin):
    ticket = resource(session, Ticket, ticket_id)
    employee = access.employee_for(session, actor, ticket.employee_id, support=True)
    service.identity_reference(session, data.recruiter_id, ["hr", "hr_head"], employee.branch_code)
    from .support import participant

    participant(session, ticket, ticket.recruiter_id, "former")
    ticket.reason, ticket.recruiter_id = data.reason, data.recruiter_id
    participant(session, ticket, ticket.recruiter_id, "assigned")
    service.audit(session, actor, "ticket.update", ticket.id)
    return {"ok": True}


@router.delete("/tickets/{ticket_id}")
def archive_ticket(ticket_id: str, session: Session, actor: Admin):
    ticket = resource(session, Ticket, ticket_id)
    access.employee_for(session, actor, ticket.employee_id, support=True)
    from .calendar import now

    # Archiving is a separate administrative outcome, never an employee-confirmed resolution.
    ticket.status, ticket.human_mode = "closed", False
    ticket.outcome, ticket.closed_at = "archived", now(session)
    ticket.context = {**ticket.context, "_archived": True}
    service.audit(session, actor, "ticket.archive", ticket.id)
    return {"ok": True}


@router.post("/tickets/{ticket_id}/restore")
def restore_ticket(ticket_id: str, session: Session, actor: Admin):
    ticket = resource(session, Ticket, ticket_id)
    access.employee_for(session, actor, ticket.employee_id, support=True)
    ticket.context = {**ticket.context, "_archived": False}
    service.audit(session, actor, "ticket.restore", ticket.id)
    return {"ok": True}
