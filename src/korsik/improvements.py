"""Derived observations and privacy-thresholded snapshots; never drives employee workflows."""

import hashlib
import json
from collections import defaultdict
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import select

from . import access
from .calendar import now
from .extension_models import ImprovementInsight, ImprovementObservation, ImprovementReport, ManagementTask
from .extension_schemas import ImprovementPolicy
from .models import Employee, FeedbackAnalysis, HelpSession, State, SurveyAnswer, SurveyRun


def policy(session):
    stored = session.get(State, "improvement_policy")
    return ImprovementPolicy.model_validate(stored.value if stored else {})


def signature(definition):
    return hashlib.sha256(json.dumps(definition, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def upsert(session, key, revision, values):
    observation = session.get(ImprovementObservation, key)
    if observation and observation.source_revision > revision:
        return
    if not observation:
        observation = ImprovementObservation(source_key=key)
        session.add(observation)
    for name, value in values.items():
        setattr(observation, name, value)
    observation.source_revision = revision


def refresh(session, config):
    # Run after answers have committed, while preparing analytics. Failures here cannot undo an answer.
    for help in session.scalars(select(HelpSession)):
        employee = session.get(Employee, help.employee_id)
        status = help.context.get("answer_status")
        if not status:
            status = (
                "answered"
                if help.status in {"awaiting_feedback", "resolved", "recipient_choice"}
                else "unknown"
            )
        mode = help.context.get(
            "mode", "demo" if (help.processor_version or "").startswith("stub/") else "real"
        )
        topics = sorted(set(help.context.get("topics", [])) & set(config.approved_topics))
        helpful = help.context.get("helpful")
        if not help.context.get("branch_code_at_event"):
            status, topics = "unknown_scope", []
        category = (
            "knowledge_no_answer"
            if status == "not_found"
            else ("knowledge_unhelpful" if status == "answered" and helpful is False else "none")
        )
        upsert(
            session,
            f"help:{help.id}:{mode}",
            help.context.get("revision", 0),
            {
                "employee_id": employee.id,
                "branch_code": help.context.get("branch_code_at_event", employee.branch_code),
                "category": category,
                "topic": topics[0] if topics else "",
                "topics": topics,
                "occurred_at": help.created_at,
                "mode": mode,
                "terminal_status": status,
                "helpful": helpful,
                "question_key": None,
                "comment_present": False,
            },
        )
    for answer in session.scalars(select(SurveyAnswer)):
        run = session.get(SurveyRun, answer.run_id)
        employee = session.get(Employee, run.employee_id)
        mode = run.data_mode
        version = signature(run.definition)
        topics = []
        for rule in config.process_rules:
            if not run.branch_code_at_start:
                continue
            if (
                rule.survey_id != run.definition["id"]
                or rule.survey_version != version
                or rule.question_id != answer.question_id
            ):
                continue
            value = answer.value.get("answer")
            negative = (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and rule.below is not None
                and value < rule.below
            )
            selected = value if isinstance(value, list) else [value]
            if (
                negative or any(v in rule.negative_options for v in selected)
            ) and rule.topic in config.process_topics:
                # Include version/question in grouping: incompatible questionnaires never share a rate.
                topics.append(f"{rule.topic}:{version}:{answer.question_id}")
        upsert(
            session,
            f"answer:{answer.id}:{mode}",
            1 + int(bool(answer.comment)),
            {
                "employee_id": employee.id,
                "branch_code": run.branch_code_at_start or employee.branch_code,
                "category": "process_friction" if topics else "none",
                "topic": topics[0] if topics else "",
                "topics": topics,
                "occurred_at": answer.created_at,
                "mode": mode,
                "terminal_status": "survey_answer",
                "helpful": None,
                "question_key": f"{version}:{answer.question_id}",
                "comment_present": bool(answer.comment),
            },
        )
    session.flush()


def build_report(session, actor, settings, *, end=None, save=True):
    access.require(actor.active and "hr_head" in actor.roles)
    config = policy(session)
    if not config.enabled or (config.mode == "demo" and not settings.enable_demo):
        raise HTTPException(409, "Отчёт отключён: согласуйте политику или включите демонстрационный режим")
    refresh(session, config)
    end = end or now(session)
    start = end - timedelta(days=config.window_days)
    rows = [
        o
        for o in session.scalars(
            select(ImprovementObservation).where(
                ImprovementObservation.mode == config.mode,
                ImprovementObservation.occurred_at >= start,
                ImprovementObservation.occurred_at < end,
            )
        )
        if access.in_scope(actor, o.branch_code)
    ]
    groups = defaultdict(list)
    for row in rows:
        if row.category != "none":
            for topic in row.topics:
                groups[(row.branch_code, row.category, topic)].append(row)
    cards = []
    for (branch, category, topic), group in sorted(groups.items()):
        people = {x.employee_id for x in group}
        if len(people) < config.min_employees:
            continue
        insight = session.scalar(
            select(ImprovementInsight).where(
                ImprovementInsight.branch_code == branch,
                ImprovementInsight.category == category,
                ImprovementInsight.topic == topic,
                ImprovementInsight.mode == config.mode,
            )
        )
        if not insight:
            insight = ImprovementInsight(branch_code=branch, category=category, topic=topic, mode=config.mode)
            session.add(insight)
            session.flush()
        if insight.snoozed_until and insight.snoozed_until > end:
            continue
        active = session.scalar(
            select(ManagementTask).where(
                ManagementTask.source_insight_id == insight.id,
                ManagementTask.status.in_(["assigned", "in_progress", "blocked"]),
            )
        )
        from .management import can_read

        if category == "process_friction":
            comparable = [
                r for r in rows if r.branch_code == branch and r.question_key == group[0].question_key
            ]
        elif category == "knowledge_unhelpful":
            comparable = [
                r
                for r in rows
                if r.branch_code == branch
                and topic in r.topics
                and r.terminal_status == "answered"
                and r.helpful is not None
            ]
        else:
            comparable = [
                r
                for r in rows
                if r.branch_code == branch
                and topic in r.topics
                and r.terminal_status in {"answered", "not_found"}
            ]
        denominator = len({r.source_key for r in comparable}) or None

        cards.append(
            {
                "id": insight.id,
                "branch_code": branch,
                "category": category,
                "topic": topic.split(":", 1)[0],
                "topic_key": topic,
                "cases": len({x.source_key for x in group}),
                "employees": len(people),
                "rate": round(100 * len(group) / denominator, 2) if denominator else None,
                "denominator": denominator,
                "rate_reason": "Одна тема и логический источник; для опроса — точная версия вопроса"
                if denominator
                else "Нет сопоставимого знаменателя",
                "active_task_id": active.id if active and can_read(session, actor, active) else None,
                "work_in_progress": bool(active),
                "suggestion": "Проверьте материалы по теме"
                if category.startswith("knowledge")
                else "Проверьте процесс по теме",
            }
        )
    # Do not expose technical counters or unclassified counts for sub-threshold groups.
    privacy_ok = len({r.employee_id for r in rows}) >= config.min_employees
    text_answer_ids = set()
    for row in rows:
        if not row.source_key.startswith("answer:"):
            continue
        answer = session.get(SurveyAnswer, row.source_key.split(":")[1])
        run = session.get(SurveyRun, answer.run_id)
        question = next((q for q in run.definition["questions"] if q["id"] == answer.question_id), {})
        if answer.comment or question.get("kind") == "text":
            text_answer_ids.add(answer.id)
    analysed_ids = {
        a.answer_id
        for a in session.scalars(
            select(FeedbackAnalysis).where(FeedbackAnalysis.answer_id.in_(text_answer_ids))
        )
        if (a.processor_version.startswith("stub/")) == (config.mode == "demo")
    }
    snapshot = {
        "mode": config.mode,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "branch_codes": list(actor.branch_codes),
        "policy": config.model_dump(mode="json"),
        "items": cards,
        "coverage": {
            "survey_texts_requiring_analysis": len(text_answer_ids) if privacy_ok else None,
            "survey_texts_analysed": len(analysed_ids) if privacy_ok else None,
            "ml_analysis_coverage_percent": round(100 * len(analysed_ids) / len(text_answer_ids), 2)
            if privacy_ok and text_answer_ids
            else None,
            "help_feedback": "explicit",
            "process_rules": "configured" if config.process_rules else "not_configured",
            "technical_errors": sum(
                r.terminal_status in {"unavailable", "blocked", "unknown", "unknown_scope"} for r in rows
            )
            if privacy_ok
            else None,
            "unclassified": sum(not r.topics for r in rows) if privacy_ok else None,
            "note": "Неполные данные не означают отсутствие проблем",
        },
        "mapping_version": "topics/1",
    }
    if not save:
        return snapshot
    report = ImprovementReport(actor_id=actor.id, mode=config.mode, snapshot=snapshot, created_at=end)
    session.add(report)
    session.flush()
    return {"id": report.id, **snapshot}


def read_report(session, actor, report_id):
    access.require(actor.active and "hr_head" in actor.roles)
    report = session.get(ImprovementReport, report_id)
    if not report or report.actor_id != actor.id:
        raise HTTPException(404, "Снимок не найден")
    snapshot = report.snapshot
    current = policy(session)
    if not current.enabled or snapshot["policy"] != current.model_dump(mode="json"):
        raise HTTPException(409, "Политика изменилась; сформируйте новый снимок")
    if not access.scope_contains(actor, snapshot["branch_codes"]):
        raise HTTPException(404, "Снимок больше не доступен в текущей области")
    if not all(access.in_scope(actor, x["branch_code"]) for x in snapshot["items"]):
        raise HTTPException(404, "Снимок больше не доступен в текущей области")
    return {"id": report.id, **snapshot}


def followup(session, actor, task, settings):
    from .management import can_read

    if not can_read(session, actor, task):
        raise HTTPException(404)
    if (
        task.status != "completed"
        or not task.completed_at
        or not task.result
        or task.result["kind"] == "no_change_needed"
    ):
        return {"status": "Нет зафиксированного изменения", "improvement": None}
    if not task.source_insight_id:
        return {
            "status": "Изменение зафиксировано; нет сопоставимого аналитического основания",
            "improvement": None,
        }
    if now(session) < task.completed_at + timedelta(days=7):
        return {"status": "Наблюдение продолжается", "improvement": None}
    config = policy(session)
    refresh(session, config)
    insight = session.get(ImprovementInsight, task.source_insight_id)
    if not config.enabled or config.mode != insight.mode:
        return {"status": "Отчёт наблюдения отключён", "improvement": None}
    periods = {}
    for label, start, end in [
        ("before", task.completed_at - timedelta(days=7), task.completed_at),
        ("after", task.completed_at, task.completed_at + timedelta(days=7)),
    ]:
        rows = [
            x
            for x in session.scalars(
                select(ImprovementObservation).where(
                    ImprovementObservation.branch_code == task.branch_code,
                    ImprovementObservation.mode == insight.mode,
                    ImprovementObservation.occurred_at >= start,
                    ImprovementObservation.occurred_at < end,
                )
            )
            if insight.topic in x.topics and x.category == insight.category
        ]
        n = len({x.employee_id for x in rows})
        periods[label] = {
            "days": 7,
            "cases": len(rows) if n >= config.min_employees else None,
            "employees": n if n >= config.min_employees else None,
        }
    return {
        "status": "Недостаточно данных"
        if any(p["cases"] is None for p in periods.values())
        else "Наблюдение завершено",
        "periods": periods,
        "improvement": None,
        "note": "Сравнение наблюдений не доказывает причинный эффект",
    }
