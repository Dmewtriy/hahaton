"""Administrative use cases. Scope is enforced here, not delegated to the browser."""

from fastapi import HTTPException
from sqlalchemy import func, select

from . import access, employees, scenarios
from .calendar import add_months, now
from .db import utcnow
from .models import (
    AuditEvent,
    Branch,
    Department,
    Employee,
    Identity,
    Outbox,
    Scenario,
    ScheduledStep,
    SurveyRun,
    SurveyTemplate,
    Ticket,
    uid,
)
from .schemas import EmployeeCreate, SurveyDefinition


def audit(session, actor, action, resource_id):
    session.add(AuditEvent(actor_id=actor.id, action=action, resource_id=resource_id))


def require_hr(actor):
    access.require(access.is_hr(actor), "Действие доступно HR-администратору")


def check_scope(actor, codes):
    require_hr(actor)
    access.require(access.scope_contains(actor, codes), "Объект вне вашей области доступа")


def check_branches(session, actor, codes):
    check_scope(actor, codes)
    for code in codes:
        if code != "*":
            branch = session.get(Branch, code)
            if not branch or not branch.active:
                raise HTTPException(422, "Выберите действующую площадку")


def deactivate_employee(session, employee):
    """Stop all pending dialogue work while retaining answers and history."""
    employee.active, employee.status = False, "stopped"
    employee.dialogue = {}
    from .models import RouteRun

    for route in session.scalars(
        select(RouteRun).where(RouteRun.employee_id == employee.id, RouteRun.status != "completed")
    ):
        route.status = "cancelled"
    session.get(Identity, employee.identity_id).active = False
    for step in session.scalars(
        select(ScheduledStep).where(
            ScheduledStep.employee_id == employee.id,
            ScheduledStep.status.in_(["pending", "manual", "queued", "enqueued"]),
        )
    ):
        step.status = "cancelled"
    for run in session.scalars(
        select(SurveyRun).where(
            SurveyRun.employee_id == employee.id,
            SurveyRun.status == "active",
        )
    ):
        run.status = "cancelled"
    for ticket in session.scalars(
        select(Ticket).where(
            Ticket.employee_id == employee.id,
            Ticket.status != "closed",
        )
    ):
        ticket.status, ticket.human_mode, ticket.closed_at = "closed", False, now(session)
    for item in session.scalars(
        select(Outbox).where(
            Outbox.recipient_id == employee.identity_id,
            Outbox.status.in_(["pending", "retry"]),
        )
    ):
        item.status = "cancelled"


def revision_check(entity, expected):
    if expected is not None and entity.revision != expected:
        raise HTTPException(409, "Запись уже изменена другим пользователем. Обновите страницу")


def identity_reference(session, identity_id, roles, branch_code):
    if not identity_id:
        return None
    identity = session.get(Identity, identity_id)
    if (
        not identity
        or not identity.active
        or not set(identity.roles) & set(roles)
        or not access.in_scope(identity, branch_code)
    ):
        raise HTTPException(422, "Выбранный руководитель или рекрутер недоступен этой площадке")
    return identity


def ensure_department(session, name, branch_code, department_id=None):
    if department_id:
        department = session.get(Department, department_id)
        if not department or not department.active or department.branch_code != branch_code:
            raise HTTPException(422, "Выберите действующее подразделение этой площадки")
        return department
    department = session.scalar(
        select(Department).where(Department.branch_code == branch_code, Department.name == name.strip())
    )
    if department and not department.active:
        raise HTTPException(422, "Подразделение в архиве. Восстановите его или выберите другое")
    if not department:
        department = Department(name=name.strip(), branch_code=branch_code)
        session.add(department)
        session.flush()
    return department


def save_employee(session, actor, data, employee_id=None):
    check_scope(actor, [data.branch_code])
    branch = session.get(Branch, data.branch_code)
    if not branch or not branch.active:
        raise HTTPException(422, "Площадка не найдена или находится в архиве")
    identity_reference(session, data.recruiter_id, ["hr", "hr_head"], branch.code)
    data = data.model_copy(update={"manager_id": employees.resolve_manager(session, data)})
    identity_reference(session, data.manager_id, ["manager"], branch.code)
    department = ensure_department(session, data.department, branch.code, data.department_id)
    end = data.probation_end or add_months(data.hire_date, 3)
    if end < data.hire_date:
        raise HTTPException(422, "Окончание адаптации должно быть не раньше даты выхода")
    if employee_id:
        employee = access.employee_for(session, actor, employee_id, support=True)
        old_assignment = employees.assignment(employee)
        revision_check(employee, data.revision)
        was_active = employee.active
        old_plan = (
            employee.hire_date,
            employee.probation_end,
            employee.branch_code,
            employee.category,
            employee.timezone,
        )
        for key in (
            "name",
            "branch_code",
            "category",
            "manager_id",
            "recruiter_id",
            "hire_date",
            "adaptation_plan_url",
            "it_access_url",
        ):
            setattr(employee, key, getattr(data, key))
        employee.timezone = data.timezone or branch.timezone
        employee.probation_end = end
        employee.revision += 1
        identity = session.get(Identity, employee.identity_id)
        identity.name = data.name
        identity.branch_codes = [branch.code]
        new_plan = (
            employee.hire_date,
            employee.probation_end,
            employee.branch_code,
            employee.category,
            employee.timezone,
        )
        if old_plan != new_plan:
            scenarios.rebuild_schedule(session, employee)
    else:
        was_active = True
        plain = EmployeeCreate.model_validate(
            {key: getattr(data, key) for key in EmployeeCreate.model_fields}
        )
        employee = employees.create_employee(session, actor, plain)
        identity = session.get(Identity, employee.identity_id)
        scenarios.rebuild_schedule(session, employee)
    employee.department = department.name
    employee.department_id = department.id
    if employee_id:
        employees.record_assignment(session, actor, employee, old_assignment)
        if old_assignment != employees.assignment(employee):
            for ticket in session.scalars(
                select(Ticket).where(Ticket.employee_id == employee.id, Ticket.status != "closed")
            ):
                from .support import participant

                participant(session, ticket, ticket.recruiter_id, "former")
                ticket.recruiter_id = employee.recruiter_id
                participant(session, ticket, employee.recruiter_id, "assigned")
    employee.active = data.active
    identity.active = data.active
    if not data.active:
        deactivate_employee(session, employee)
    elif not was_active:
        employee.status = "active" if identity.max_user_id else "invited"
        scenarios.rebuild_schedule(session, employee)
    audit(session, actor, "employee.update" if employee_id else "employee.create", employee.id)
    session.flush()
    return employee


def employee_dto(session, employee):
    data = employees.employee_summary(employee)
    recruiter = session.get(Identity, employee.recruiter_id)
    manager = session.get(Identity, employee.manager_id) if employee.manager_id else None
    branch = session.get(Branch, employee.branch_code)
    identity = session.get(Identity, employee.identity_id)
    data.update(
        revision=employee.revision,
        department_id=employee.department_id,
        recruiter_name=recruiter.name if recruiter else "Не назначен",
        manager_name=manager.name if manager else "Не назначен",
        branch_name=branch.name,
        max_bound=bool(identity.max_user_id),
        address_style=employee.address_style,
        adaptation_plan_url=employee.adaptation_plan_url,
        it_access_url=employee.it_access_url,
    )
    from .onboarding import phase

    data["onboarding_phase"] = phase(session, employee)
    data["stage_label"] = scenarios.stage_label(session, employee)
    return data


def branch_dto(session, branch):
    scenario = session.get(Scenario, branch.scenario_id) if branch.scenario_id else None
    count = session.scalar(
        select(func.count())
        .select_from(Employee)
        .where(Employee.branch_code == branch.code, Employee.active.is_(True))
    )
    return {
        "code": branch.code,
        "name": branch.name,
        "timezone": branch.timezone,
        "workdays": branch.workdays,
        "holidays": branch.holidays,
        "working_dates": branch.working_dates,
        "resources": branch.resources,
        "resource_labels": branch.resource_labels,
        "scenario_id": branch.scenario_id,
        "scenario_name": scenario.name if scenario else None,
        "active": branch.active,
        "revision": branch.revision,
        "employee_count": count,
        "workplace": branch.workplace,
        "late_join_policy": branch.late_join_policy,
        "support_recipient_ids": branch.support_recipient_ids,
    }


def save_branch(session, actor, data, code):
    check_scope(actor, [code])
    if code != data.code:
        raise HTTPException(422, "Код площадки нельзя изменить")
    if data.scenario_id:
        scenario = session.get(Scenario, data.scenario_id)
        if (
            not scenario
            or not scenario.active
            or scenario.kind != "onboarding"
            or not ("*" in scenario.branch_codes or code in scenario.branch_codes)
        ):
            raise HTTPException(422, "Сценарий недоступен этой площадке")
    branch = session.get(Branch, code)
    old = None
    if branch:
        revision_check(branch, data.revision)
        old = (branch.scenario_id, branch.workdays, branch.holidays, branch.working_dates, branch.active)
        branch.revision += 1
    else:
        branch = Branch(code=code)
        session.add(branch)
    for key, value in data.model_dump(mode="json", exclude={"revision"}).items():
        setattr(branch, key, value)
    session.flush()
    new = (branch.scenario_id, branch.workdays, branch.holidays, branch.working_dates, branch.active)
    if old != new:
        for employee in session.scalars(
            select(Employee).where(Employee.branch_code == code).with_for_update()
        ):
            scenarios.rebuild_schedule(session, employee)
    audit(session, actor, "branch.save", code)
    return branch


def save_department(session, actor, data, department_id=None):
    check_scope(actor, [data.branch_code])
    branch = session.get(Branch, data.branch_code)
    if not branch or not branch.active:
        raise HTTPException(422, "Площадка недоступна")
    identity_reference(session, data.manager_id, ["manager"], data.branch_code)
    if department_id:
        department = session.get(Department, department_id)
        if not department:
            raise HTTPException(404)
        check_scope(actor, [department.branch_code])
        revision_check(department, data.revision)
        if department.branch_code != data.branch_code:
            raise HTTPException(422, "Перенос подразделения между площадками не поддерживается")
        department.revision += 1
    else:
        department = Department(branch_code=data.branch_code)
        session.add(department)
    department.name, department.manager_id = data.name.strip(), data.manager_id
    session.flush()
    for employee in session.scalars(select(Employee).where(Employee.department_id == department.id)):
        employee.department = department.name
        employee.revision += 1
    audit(session, actor, "department.save", department.id)
    return department


def department_dto(session, department):
    return {
        "id": department.id,
        "name": department.name,
        "branch_code": department.branch_code,
        "branch_name": session.get(Branch, department.branch_code).name,
        "manager_id": department.manager_id,
        "active": department.active,
        "revision": department.revision,
        "employee_count": session.scalar(
            select(func.count())
            .select_from(Employee)
            .where(Employee.department_id == department.id, Employee.active.is_(True))
        ),
    }


def save_survey(session, actor, data, template_id=None):
    check_branches(session, actor, data.branch_codes)
    if template_id:
        template = session.get(SurveyTemplate, template_id)
        if not template:
            raise HTTPException(404)
        check_scope(actor, template.branch_codes)
        revision_check(template, data.revision)
        template.revision += 1
    else:
        template = SurveyTemplate(id=uid(), created_by=actor.id)
        session.add(template)
    definition = SurveyDefinition(
        id=template.id,
        title=data.title,
        questions=data.questions,
        reminder_days=data.reminder_days,
        kind=data.kind,
        pass_percent=data.pass_percent,
    )
    template.title, template.description = data.title, data.description
    template.branch_codes = data.branch_codes
    template.definition = definition.model_dump(mode="json")
    template.updated_at = utcnow()
    session.flush()
    audit(session, actor, "survey_template.save", template.id)
    return template


def survey_dto(template):
    return {
        "id": template.id,
        "title": template.title,
        "description": template.description,
        "definition": template.definition,
        "branch_codes": template.branch_codes,
        "revision": template.revision,
        "active": template.active,
        "question_count": len(template.definition["questions"]),
    }


def visible_scoped(actor, codes):
    return access.is_hr(actor) and (
        "*" in codes or "*" in actor.branch_codes or bool(set(codes) & set(actor.branch_codes))
    )


def archive(session, actor, entity, active=False):
    codes = [entity.branch_code] if hasattr(entity, "branch_code") else entity.branch_codes
    check_scope(actor, codes)
    entity.active = active
    if hasattr(entity, "revision"):
        entity.revision += 1
    audit(session, actor, "object.restore" if active else "object.archive", str(entity.id))
