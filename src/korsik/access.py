import hashlib
import secrets

from fastapi import HTTPException
from sqlalchemy import select

from .db import utcnow
from .models import Employee, Identity, WebSession


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_token():
    return secrets.token_urlsafe(32)


def require(condition: bool, message: str = "Недостаточно прав"):
    if not condition:
        raise HTTPException(403, message)


def in_scope(actor: Identity, branch: str):
    return "*" in actor.branch_codes or branch in actor.branch_codes


def is_hr(actor: Identity):
    return bool(set(actor.roles) & {"hr", "hr_head"})


def is_admin(actor: Identity):
    return actor.active and (is_hr(actor) or actor.manage_access)


def can_use_workspace(actor: Identity):
    return actor.active and (is_admin(actor) or "manager" in actor.roles)


def can_manage_people(actor: Identity):
    return actor.manage_access or is_hr(actor)


def can_manage_identity(actor: Identity, target: Identity):
    if actor.manage_access:
        return True
    return (
        is_hr(actor)
        and target.id != "bootstrap"
        and not target.manage_access
        and scope_contains(actor, target.branch_codes)
    )


def scope_contains(actor: Identity, branch_codes):
    return bool(branch_codes) and all(in_scope(actor, code) for code in branch_codes)


def can_configure(actor: Identity, branch: str):
    return bool(set(actor.roles) & {"hr", "hr_head"}) and in_scope(actor, branch)


def can_view_employee(actor: Identity, employee: Employee):
    if employee.identity_id == actor.id and "employee" in actor.roles:
        return True
    if not in_scope(actor, employee.branch_code):
        return False
    return (
        is_hr(actor)
        or ("manager" in actor.roles and employee.manager_id == actor.id)
        or "hr_head" in actor.roles
    )


def can_support(actor: Identity, employee: Employee):
    return is_hr(actor) and in_scope(actor, employee.branch_code)


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
        if not actor:
            web_session = session.get(WebSession, token_hash(token))
            if web_session and web_session.expires_at > utcnow():
                actor = session.get(Identity, web_session.identity_id)
    if not actor or not actor.active:
        raise HTTPException(401, "Неверный или заблокированный ключ")
    return actor
