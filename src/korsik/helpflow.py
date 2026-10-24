"""Durable AI-help state, independent from survey answer collection."""

from .access import require
from .calendar import now
from .models import HelpSession, SurveyRun
from .queue import send


def begin(session, employee, question, run, help_id=None):
    help = session.get(HelpSession, help_id) if help_id else None
    if help:
        require(help.employee_id == employee.id)
        help.question = question
    else:
        from .routes import active

        route = active(session, employee) if not run else None
        help = HelpSession(
            employee_id=employee.id,
            question=question,
            created_at=now(session),
            context={
                "survey_run_id": run.id if run else None,
                "question_index": run.position if run else None,
                "route_run_id": route.id if route else None,
                "route_position": route.position if route else None,
            },
        )
        session.add(help)
        session.flush()
    help.context = {**help.context, "attempt": help.context.get("attempt", 0) + 1}
    employee.dialogue = {**employee.dialogue, "active_help": help.id}
    return help


def finish(session, employee, help, status, text, version):
    help.answer = text
    help.processor_version = version
    if status == "clarification":
        help.status = "awaiting_clarification"
    elif status == "answered":
        help.status = "awaiting_feedback"
        send(
            session,
            employee.identity_id,
            "Удалось решить вопрос?",
            f"help-feedback:{help.id}:{help.context.get('attempt', 1)}",
            now(session),
            buttons=[
                {
                    "text": "Да, вопрос решён",
                    "payload": f"help:{help.id}:{help.context.get('attempt', 1)}:yes",
                },
                {
                    "text": "Нет, нужна помощь HR",
                    "payload": f"help:{help.id}:{help.context.get('attempt', 1)}:no",
                },
            ],
        )
    else:
        help.status = "escalated" if help.ticket_id else "error"
        send(
            session,
            employee.identity_id,
            "Можно повторить запрос или продолжить обращение HR. Возврат к анкете: /resume.",
            f"help-retry:{help.id}:{help.context.get('attempt', 1)}",
            now(session),
            buttons=[
                {
                    "text": "Повторить запрос",
                    "payload": f"help:{help.id}:{help.context.get('attempt', 1)}:retry",
                }
            ]
            + (
                [{"text": "Дополнить обращение HR", "payload": "case-reply:" + help.ticket_id}]
                if help.ticket_id
                else [
                    {"text": "Передать HR", "payload": f"help:{help.id}:{help.context.get('attempt', 1)}:no"}
                ]
            ),
        )


def callback(runtime, session, employee, payload, key):
    if not payload.startswith("help:"):
        return False
    parts = payload.split(":")
    if len(parts) != 4:
        return True
    _, help_id, attempt, action = parts
    help = session.get(HelpSession, help_id)
    require(bool(help and help.employee_id == employee.id))
    if (
        attempt != str(help.context.get("attempt", 1))
        or action == "retry"
        and help.status not in {"error", "escalated"}
    ):
        send(
            session,
            employee.identity_id,
            "Это действие устарело. Используйте текущие кнопки или /resume.",
            "help-stale:" + key,
            now(session),
        )
        return True
    if action == "retry":
        from .bot import ask

        run = (
            session.get(SurveyRun, help.context.get("survey_run_id"))
            if help.context.get("survey_run_id")
            else None
        )
        ask(
            runtime,
            session,
            employee,
            help.question,
            key,
            run if run and run.status == "active" else None,
            help_id=help.id,
        )
    elif action in {"yes", "no"}:
        if help.status not in {"awaiting_feedback", "error"}:
            send(
                session,
                employee.identity_id,
                "Это действие уже обработано. Опрос: /resume.",
                "help-stale:" + key,
                now(session),
            )
            return True
        employee.dialogue = {k: v for k, v in employee.dialogue.items() if k != "active_help"}
        if action == "yes":
            require(help.status == "awaiting_feedback")
            help.status = "resolved"
            send(
                session,
                employee.identity_id,
                "Отлично, помощь завершена. Продолжить опрос: /resume.",
                "help-resolved:" + key,
                now(session),
            )
        else:
            from .support import choose_recipient

            help.status = "recipient_choice"
            choose_recipient(
                session,
                employee,
                key,
                text=help.question,
                context={
                    "help_id": help.id,
                    "question": help.question,
                    "ai_answer": help.answer,
                    **help.context,
                },
                ticket_id=help.ticket_id,
            )
    return True


def consume(runtime, session, employee, text, key):
    help_id = employee.dialogue.get("active_help")
    help = session.get(HelpSession, help_id) if help_id else None
    if not help:
        return False
    if help.status == "awaiting_clarification":
        from .bot import ask

        run = (
            session.get(SurveyRun, help.context.get("survey_run_id"))
            if help.context.get("survey_run_id")
            else None
        )
        ask(
            runtime,
            session,
            employee,
            help.question + "\nУточнение: " + text,
            key,
            run if run and run.status == "active" else None,
            help_id=help.id,
        )
        return True
    if help.status == "awaiting_feedback":
        if text.casefold() in {"да", "нет"}:
            return callback(
                runtime,
                session,
                employee,
                f"help:{help.id}:{help.context.get('attempt', 1)}:"
                + ("yes" if text.casefold() == "да" else "no"),
                key,
            )
        send(
            session,
            employee.identity_id,
            "Выберите, помог ли ответ: Да / Нет. Вернуться к анкете: /resume.",
            "help-wait:" + key,
            now(session),
            buttons=[
                {"text": "Да", "payload": f"help:{help.id}:{help.context.get('attempt', 1)}:yes"},
                {"text": "Нет", "payload": f"help:{help.id}:{help.context.get('attempt', 1)}:no"},
            ],
        )
        return True
    return False
