"""Backend policy turns an external analysis into scoped human attention signals."""

from .models import State
from .schemas import EscalationRules


def processing_status(session, answer):
    from sqlalchemy import select

    from .models import FeedbackAnalysis, Job

    if not answer.comment and answer.value.get("kind") != "text":
        return {"status": "not_required", "version": None, "error": None}
    analysis = session.scalar(select(FeedbackAnalysis).where(FeedbackAnalysis.answer_id == answer.id))
    if analysis:
        return {"status": "completed", "version": analysis.processor_version, "error": None}
    job = session.scalar(select(Job).where(Job.dedupe_key == "analyze:" + answer.id))
    return {
        "status": job.status if job else "missing",
        "version": None,
        "error": job.last_error if job else None,
    }


def apply_policy(session, employee, analysis, result, manager_signal=False):
    policy = session.get(State, "escalation_rules")
    rules = EscalationRules.model_validate(policy.value if policy else {})
    rank = {"none": 0, "low": 1, "medium": 2, "high": 3}
    flagged = (
        rules.enabled
        and result.needs_hr
        and rank[result.risk] >= rank[rules.minimum_risk]
        and (not rules.topics or set(rules.topics) & set(result.topics))
    )
    if flagged:
        from .tickets import open_ticket

        open_ticket(
            session,
            employee,
            "Сигнал по обратной связи",
            {"answer_id": analysis.answer_id, "analysis": result.model_dump()},
            f"answer:{analysis.answer_id}",
            human=False,
        )
    if manager_signal or flagged and rules.recommend_meeting:
        from .meetings import recommend

        recommend(session, employee, analysis)
