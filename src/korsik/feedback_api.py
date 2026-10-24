"""HR-visible processing completeness, including failures independent of saved answers."""

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from . import access, feedback
from .admin_service import audit
from .calendar import now
from .dependencies import Admin, Session
from .models import Employee, Identity, Outbox, SurveyAnswer, SurveyRun, Ticket
from .queue import enqueue

router = APIRouter(prefix="/api/admin/feedback", tags=["Feedback processing"])


@router.get("")
def status(session: Session, actor: Admin):
    access.require(access.is_hr(actor))
    result = []
    for answer in session.scalars(select(SurveyAnswer).order_by(SurveyAnswer.created_at.desc())):
        run = session.get(SurveyRun, answer.run_id)
        employee = session.get(Employee, run.employee_id)
        if not access.can_support(actor, employee):
            continue
        state = feedback.processing_status(session, answer)
        if state["status"] != "not_required":
            result.append(
                {
                    "answer_id": answer.id,
                    "employee_name": employee.name,
                    "survey_title": run.definition["title"],
                    "created_at": answer.created_at,
                    **state,
                }
            )
    signals = []
    for ticket in session.scalars(select(Ticket).order_by(Ticket.created_at.desc())):
        employee = session.get(Employee, ticket.employee_id)
        if not access.can_support(actor, employee):
            continue
        item = session.scalar(select(Outbox).where(Outbox.dedupe_key == f"ticket:{ticket.id}:notify"))
        recipient = session.get(Identity, ticket.recruiter_id)
        state = (
            "not_created"
            if not item
            else "waiting_binding"
            if item.status in {"pending", "retry"} and not recipient.max_user_id
            else item.status
        )
        signals.append(
            {
                "ticket_id": ticket.id,
                "employee_name": employee.name,
                "recipient_name": recipient.name,
                "status": state,
                "error": item.last_error if item else None,
            }
        )
    return {"answers": result, "notifications": signals}


@router.post("/{answer_id}/retry")
def retry(answer_id: str, session: Session, actor: Admin):
    answer = session.get(SurveyAnswer, answer_id)
    if not answer:
        raise HTTPException(404, "Ответ не найден")
    run = session.get(SurveyRun, answer.run_id)
    employee = session.get(Employee, run.employee_id)
    access.require(access.can_support(actor, employee))
    if feedback.processing_status(session, answer)["status"] not in {"failed", "missing"}:
        raise HTTPException(409, "Повтор доступен только для отсутствующего или неудачного анализа")
    question = next(q for q in run.definition["questions"] if q["id"] == answer.question_id)
    job = enqueue(
        session,
        "analyze",
        {"answer_id": answer.id, "employee_id": employee.id, "question": question["text"]},
        "analyze:" + answer.id,
        now(session),
    )
    job.status = "pending"
    job.attempts = 0
    job.last_error = None
    job.due_at = now(session)
    audit(session, actor, "feedback.retry", answer.id)
    return {"status": "pending"}
