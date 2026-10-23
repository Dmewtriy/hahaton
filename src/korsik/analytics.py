from collections import Counter
from datetime import timedelta

from sqlalchemy import and_, false, or_, select

from .calendar import now
from .employees import employee_summary
from .models import Employee, FeedbackAnalysis, QuestionLog, ScheduledStep, SurveyAnswer, SurveyRun, Ticket


def visible_employees(session, actor):
    own = Employee.identity_id == actor.id if "employee" in actor.roles else false()
    branch_scope = True if "*" in actor.branch_codes else Employee.branch_code.in_(actor.branch_codes)
    work_scope = []
    if "hr" in actor.roles:
        work_scope.append(Employee.recruiter_id == actor.id)
    if "manager" in actor.roles:
        work_scope.append(Employee.manager_id == actor.id)
    if "hr_head" in actor.roles:
        work_scope.append(True)
    query = select(Employee).where(or_(own, and_(branch_scope, or_(*work_scope) if work_scope else false())))
    return list(session.scalars(query.order_by(Employee.name)))


def dashboard(session, actor, start=None, end=None):
    # Include actions at a frozen demo instant. Explicit report boundaries remain [start, end).
    end = end or now(session) + timedelta(microseconds=1)
    start = start or end - timedelta(days=7)
    employees = visible_employees(session, actor)
    ids = [e.id for e in employees]
    runs = list(session.scalars(select(SurveyRun).where(SurveyRun.employee_id.in_(ids)))) if ids else []
    steps = (
        list(session.scalars(select(ScheduledStep).where(ScheduledStep.employee_id.in_(ids)))) if ids else []
    )
    result = {
        "window": {"from": start.isoformat(), "to": end.isoformat(), "end_exclusive": True},
        "employees": [employee_summary(e) for e in employees],
        "totals": {
            "employees": len(employees),
            "active_employees": sum(e.active for e in employees),
            "stages": dict(Counter(e.stage for e in employees)),
            "active_surveys": sum(r.status == "active" for r in runs),
            "completed_surveys": sum(
                r.completed_at is not None and start <= r.completed_at < end for r in runs
            ),
            "scheduled_messages": sum(s.status in {"pending", "enqueued", "queued"} for s in steps),
        },
    }
    # Managers and employees receive progress only. No raw answers or sentiment-derived labels.
    if not set(actor.roles) & {"hr", "hr_head"}:
        return result
    analyses = (
        list(
            session.scalars(
                select(FeedbackAnalysis).where(
                    FeedbackAnalysis.employee_id.in_(ids),
                    FeedbackAnalysis.created_at >= start,
                    FeedbackAnalysis.created_at < end,
                )
            )
        )
        if ids
        else []
    )
    questions = (
        list(
            session.scalars(
                select(QuestionLog).where(
                    QuestionLog.employee_id.in_(ids),
                    QuestionLog.created_at >= start,
                    QuestionLog.created_at < end,
                )
            )
        )
        if ids
        else []
    )
    tickets = list(session.scalars(select(Ticket).where(Ticket.employee_id.in_(ids)))) if ids else []
    result["totals"].update(
        {
            "open_tickets": sum(t.status != "closed" for t in tickets),
            "tickets_created": sum(start <= t.created_at < end for t in tickets),
            "analysed_answers": len(analyses),
            "sentiments": dict(Counter(a.result["sentiment"] for a in analyses)),
            "risks": dict(Counter(a.result["risk"] for a in analyses)),
            "feedback_topics": dict(Counter(t for a in analyses for t in set(a.result["topics"]))),
            "question_topics": dict(Counter(t for q in questions for t in set(q.topics))),
            "questions": len(questions),
            "question_statuses": dict(Counter(q.status for q in questions)),
            "stub_analyses": sum(a.processor_version.startswith("stub/") for a in analyses),
        }
    )
    answers = (
        list(
            session.scalars(
                select(SurveyAnswer)
                .join(SurveyRun, SurveyAnswer.run_id == SurveyRun.id)
                .where(
                    SurveyRun.employee_id.in_(ids),
                    SurveyAnswer.created_at >= start,
                    SurveyAnswer.created_at < end,
                )
            )
        )
        if ids
        else []
    )
    ratings = {}
    for answer in answers:
        if answer.value.get("kind") == "rating":
            run = next(r for r in runs if r.id == answer.run_id)
            key = f"{run.definition['id']}:{answer.question_id}"
            ratings.setdefault(key, []).append(answer.value["answer"])
    result["ratings"] = {
        key: {"count": len(values), "mean": round(sum(values) / len(values), 2)}
        for key, values in ratings.items()
    }
    return result


def digest_text(data):
    totals = data["totals"]
    return (
        "Еженедельная сводка адаптации\n"
        f"Период: {data['window']['from']} — {data['window']['to']}\n"
        f"Сотрудников в области доступа: {totals['employees']}\n"
        f"Активных опросов: {totals['active_surveys']}\n"
        f"Завершённых опросов за период: {totals['completed_surveys']}\n"
        f"Открытых обращений: {totals.get('open_tickets', 0)}\n"
        f"Проанализировано ответов: {totals.get('analysed_answers', 0)}\n"
        f"Из них ML-заглушкой: {totals.get('stub_analyses', 0)}\n"
        f"Темы обратной связи: {totals.get('feedback_topics', {})}\n"
        "Числа рассчитаны бэкендом из БД."
    )
