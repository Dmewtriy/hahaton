import hashlib
import secrets

from fastapi import HTTPException
from sqlalchemy import select

from .models import Employee, Identity


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_token():
    return secrets.token_urlsafe(32)


def require(condition: bool, message: str = "Недостаточно прав"):
    if not condition:
        raise HTTPException(403, message)


def in_scope(actor: Identity, branch: str):
    return "*" in actor.branch_codes or branch in actor.branch_codes


def can_configure(actor: Identity, branch: str):
    return bool(set(actor.roles) & {"hr", "hr_head"}) and in_scope(actor, branch)


def can_view_employee(actor: Identity, employee: Employee):
    if employee.identity_id == actor.id and "employee" in actor.roles:
        return True
    if not in_scope(actor, employee.branch_code):
        return False
    return (
        ("hr" in actor.roles and employee.recruiter_id == actor.id)
        or ("manager" in actor.roles and employee.manager_id == actor.id)
        or "hr_head" in actor.roles
    )


def can_support(actor: Identity, employee: Employee):
    return "hr" in actor.roles and employee.recruiter_id == actor.id and in_scope(actor, employee.branch_code)


def employee_for(session, actor: Identity, employee_id: str, support=False):
    employee = session.get(Employee, employee_id)
    if not employee:
        raise HTTPException(404, "Сотрудник не найден")
    allowed = can_support(actor, employee) if support else can_view_employee(actor, employee)
    require(allowed)
    return employee


def authenticate(session, token: str, bootstrap: str):
    if secrets.compare_digest(token, bootstrap):
        actor = session.get(Identity, "bootstrap")
    else:
        actor = session.scalar(select(Identity).where(Identity.token_hash == token_hash(token)))
    if not actor or not actor.active:
        raise HTTPException(401, "Неверный или заблокированный ключ")
    return actor
