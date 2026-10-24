from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from . import access, improvements, management
from .admin_service import audit
from .calendar import now
from .dependencies import Actor, Session, runtime_dependency
from .extension_api import management_enabled
from .extension_models import ImprovementInsight
from .extension_schemas import ImprovementPolicy
from .models import State

router = APIRouter(
    prefix="/api/management/improvements", tags=["Improvements"], dependencies=[Depends(management_enabled)]
)


@router.get("/policy")
def get_policy(session: Session, actor: Actor):
    access.require(actor.manage_access or "hr_head" in actor.roles)
    return improvements.policy(session)


@router.put("/policy")
def set_policy(data: ImprovementPolicy, session: Session, actor: Actor, runtime=Depends(runtime_dependency)):
    access.require(actor.manage_access and "*" in actor.branch_codes)
    if data.mode == "demo" and data.enabled and not runtime.settings.enable_demo:
        raise HTTPException(422, "Демонстрационный режим отключён")
    session.merge(State(key="improvement_policy", value=data.model_dump(mode="json")))
    audit(session, actor, "improvement.policy", "improvement_policy")
    return data


@router.post("/reports", status_code=201)
def new_report(session: Session, actor: Actor, runtime=Depends(runtime_dependency)):
    return improvements.build_report(session, actor, runtime.settings)


@router.get("/reports/{report_id}")
def report(report_id: str, session: Session, actor: Actor):
    return improvements.read_report(session, actor, report_id)


@router.post("/insights/{insight_id}/snooze")
def snooze(insight_id: str, session: Session, actor: Actor, runtime=Depends(runtime_dependency)):
    visible = improvements.build_report(session, actor, runtime.settings, save=False)
    if not any(x["id"] == insight_id for x in visible["items"]):
        raise HTTPException(404, "Кандидат не найден")
    insight = session.scalar(
        select(ImprovementInsight).where(ImprovementInsight.id == insight_id).with_for_update()
    )
    insight.snoozed_until = now(session) + timedelta(days=7)
    audit(session, actor, "improvement.snooze", insight_id)
    return {"snoozed_until": insight.snoozed_until}


@router.get("/tasks/{task_id}/followup")
def followup(task_id: str, session: Session, actor: Actor, runtime=Depends(runtime_dependency)):
    return improvements.followup(
        session, actor, management.get_task(session, actor, task_id), runtime.settings
    )
