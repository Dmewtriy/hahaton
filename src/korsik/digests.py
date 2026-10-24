"""Auditable numeric digest. ML grouping remains an external concern."""

from datetime import timedelta

from sqlalchemy import select

from . import analytics
from .models import DigestSnapshot, Employee, FeedbackAnalysis, QuestionLog, SurveyAnswer, SurveyRun


def create_snapshot(session, actor, end, request_key, settings=None):
    existing = session.scalar(select(DigestSnapshot).where(DigestSnapshot.request_key == request_key))
    if existing:
        return existing
    start = end - timedelta(days=7)
    report = analytics.dashboard(session, actor, start, end)
    previous = analytics.dashboard(session, actor, start - timedelta(days=7), start)
    ids = [e["id"] for e in report["employees"]]
    answers = list(
        session.scalars(
            select(SurveyAnswer)
            .join(SurveyRun)
            .where(
                SurveyRun.employee_id.in_(ids),
                SurveyAnswer.created_at >= start,
                SurveyAnswer.created_at < end,
            )
        )
    )
    analyses = list(
        session.scalars(
            select(FeedbackAnalysis).where(
                FeedbackAnalysis.employee_id.in_(ids),
                FeedbackAnalysis.created_at >= start,
                FeedbackAnalysis.created_at < end,
            )
        )
    )
    questions = list(
        session.scalars(
            select(QuestionLog).where(
                QuestionLog.employee_id.in_(ids),
                QuestionLog.created_at >= start,
                QuestionLog.created_at < end,
            )
        )
    )
    runs = {r.id: r for r in session.scalars(select(SurveyRun).where(SurveyRun.employee_id.in_(ids)))}
    responding = {runs[a.run_id].employee_id for a in answers}
    report["sample"] = {
        "answers": len(answers),
        "responding_employees": len(responding),
        "analyses": len(analyses),
        "unit": "survey_answer",
        "grouping": "stored_topic_tags",
        "ml_summary": False,
    }
    comparable = ["completed_surveys", "tickets_created", "analysed_answers", "questions"]
    report["comparison"] = {
        k: {
            "current": report["totals"].get(k, 0),
            "previous": previous["totals"].get(k, 0),
            "delta": report["totals"].get(k, 0) - previous["totals"].get(k, 0),
        }
        for k in comparable
    }
    # Keep provenance inside the snapshot; current row-level access is rechecked at read time.
    provenance = {
        "branch_codes": list(actor.branch_codes),
        "employee_ids": ids,
        "answers": [
            {
                "answer_id": a.id,
                "employee_id": runs[a.run_id].employee_id,
                "run_id": a.run_id,
                "question_id": a.question_id,
            }
            for a in answers
        ],
        "analyses": [
            {
                "id": a.id,
                "answer_id": a.answer_id,
                "processor_version": a.processor_version,
                "topics": a.result.get("topics", []),
            }
            for a in analyses
        ],
        "question_ids": [q.id for q in questions],
    }
    text = analytics.digest_text(report)
    try:
        from .improvements import build_report, policy

        if policy(session).enabled:
            from types import SimpleNamespace

            with session.begin_nested():
                improvement_report = build_report(
                    session, actor, settings or SimpleNamespace(enable_demo=False), end=end
                )
                report["improvements"] = improvement_report
                text += "\nЧто улучшить: кандидатов, проходящих порог — " + str(
                    len(improvement_report["items"])
                )
        else:
            report["improvements"] = {"status": "disabled"}
            text += "\nБлок улучшений отключён: требуется согласованная политика."
    except Exception:
        report["improvements"] = {"status": "unavailable"}
        text += "\nБлок улучшений не сформирован; основная сводка доступна."
    if not answers and not questions:
        text += "\nЗа этот период новых ответов и вопросов нет."
    text += (
        f"\nОтветов за период: {len(answers)}; ответивших сотрудников: {len(responding)}."
        "\nТемы — сохранённые теги анализа. ML-кластеризация и текстовое обобщение не подключены."
    )
    snapshot = DigestSnapshot(
        recipient_id=actor.id, request_key=request_key, report=report, provenance=provenance, text=text
    )
    session.add(snapshot)
    session.flush()
    return snapshot


def can_read_snapshot(session, actor, snapshot):
    from .access import can_support, scope_contains

    return (
        actor.id == snapshot.recipient_id
        and scope_contains(actor, snapshot.provenance["branch_codes"])
        and all(
            (employee := session.get(Employee, eid)) and can_support(actor, employee)
            for eid in snapshot.provenance["employee_ids"]
        )
    )
