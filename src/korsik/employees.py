from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import select

from .access import in_scope, new_token, require, token_hash
from .calendar import add_months, now
from .models import Branch, Employee, Identity, Invitation
from .schemas import EmployeeCreate


def create_employee(session, actor, data: EmployeeCreate):
    require(bool(set(actor.roles) & {"hr", "hr_head"}) and in_scope(actor, data.branch_code))
    branch = session.get(Branch, data.branch_code)
    if not branch or not branch.active:
        raise HTTPException(422, "Площадка не найдена или выключена")
    recruiter = session.get(Identity, data.recruiter_id)
    require(
        bool(
            recruiter
            and recruiter.active
            and set(recruiter.roles) & {"hr", "hr_head"}
            and in_scope(recruiter, branch.code)
        ),
        "Рекрутер должен иметь доступ к площадке",
    )
    if data.manager_id:
        manager = session.get(Identity, data.manager_id)
        require(
            bool(manager and manager.active and "manager" in manager.roles and in_scope(manager, branch.code))
        )
    end = data.probation_end or add_months(data.hire_date, 3)
    if end < data.hire_date:
        raise HTTPException(422, "Окончание адаптации не может быть раньше выхода")
    identity = Identity(name=data.name, roles=["employee"], branch_codes=[branch.code])
    session.add(identity)
    session.flush()
    employee = Employee(
        identity_id=identity.id,
        name=data.name,
        branch_code=branch.code,
        department=data.department,
        category=data.category,
        manager_id=data.manager_id,
        recruiter_id=data.recruiter_id,
        hire_date=data.hire_date,
        probation_end=end,
        timezone=data.timezone or branch.timezone,
    )
    session.add(employee)
    session.flush()
    return employee


def invite(session, identity_id, hours):
    identity = session.get(Identity, identity_id)
    if not identity or not identity.active:
        raise HTTPException(404, "Аккаунт недоступен")
    at = now(session)
    for old in session.scalars(
        select(Invitation).where(
            Invitation.identity_id == identity_id,
            Invitation.used_at.is_(None),
        )
    ):
        old.used_at = at
    token = new_token()
    session.add(
        Invitation(
            identity_id=identity_id, token_hash=token_hash(token), expires_at=at + timedelta(hours=hours)
        )
    )
    return token


def bind(session, raw_token, max_user_id):
    invitation = session.scalar(
        select(Invitation)
        .where(
            Invitation.token_hash == token_hash(raw_token),
        )
        .with_for_update()
    )
    at = now(session)
    if not invitation or invitation.used_at or invitation.expires_at <= at:
        return None
    identity = session.get(Identity, invitation.identity_id)
    occupied = session.scalar(select(Identity).where(Identity.max_user_id == max_user_id))
    if not identity.active or (occupied and occupied.id != identity.id):
        return None
    if identity.max_user_id and identity.max_user_id != max_user_id:
        return None  # Account rebinding is deliberately an access-management operation.
    identity.max_user_id = max_user_id
    invitation.used_at = at
    employee = session.scalar(select(Employee).where(Employee.identity_id == identity.id))
    if employee:
        employee.status = "active"
    return identity


def employee_summary(employee):
    return {
        "id": employee.id,
        "identity_id": employee.identity_id,
        "name": employee.name,
        "branch_code": employee.branch_code,
        "department": employee.department,
        "category": employee.category,
        "manager_id": employee.manager_id,
        "recruiter_id": employee.recruiter_id,
        "hire_date": employee.hire_date.isoformat(),
        "probation_end": employee.probation_end.isoformat(),
        "timezone": employee.timezone,
        "status": employee.status,
        "stage": employee.stage,
        "active": employee.active,
    }
