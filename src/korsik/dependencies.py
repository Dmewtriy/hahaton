"""Shared request unit of work and authorization dependencies."""

from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session as SQLSession

from . import access
from .models import Identity

bearer = HTTPBearer(auto_error=False)


def runtime_dependency(request: Request):
    return request.app.state.runtime


def session_dependency(request: Request, runtime=Depends(runtime_dependency)):
    with runtime.db.transaction(write_lock=True) as session:
        if request.url.path.startswith("/api/reports"):
            if runtime.db.engine.dialect.name == "postgresql":
                from sqlalchemy import text

                session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
            from .calendar import now

            session.info["report_snapshot_at"] = now(session)
        yield session


Session = Annotated[SQLSession, Depends(session_dependency)]


def actor_dependency(
    session: Session,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    runtime=Depends(runtime_dependency),
):
    if not credentials:
        raise HTTPException(401, "Войдите в кабинет")
    return access.authenticate(session, credentials.credentials, runtime.settings.bootstrap_token)


Actor = Annotated[Identity, Depends(actor_dependency)]


def admin_dependency(actor: Actor):
    access.require(access.is_admin(actor), "Кабинет доступен только администраторам и HR")
    return actor


Admin = Annotated[Identity, Depends(admin_dependency)]
