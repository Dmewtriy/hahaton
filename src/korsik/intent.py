"""An explicit choice separates an ambiguous question from a saved survey answer."""

import re

from .calendar import now
from .models import uid
from .queue import send


def offer(session, employee, text, key, run=None, route=None):
    if not (
        "?" in text or re.match(r"^(где|когда|куда|кто|почему|зачем|как|можно ли|подскажите)\b", text, re.I)
    ):
        return False
    if not run and not route:
        return False
    token = uid()
    state = {
        "token": token,
        "text": text,
        "kind": "survey" if run else "route",
        "run_id": run.id if run else route.id,
        "position": run.position if run else route.position,
        "awaiting_comment": bool(run and run.awaiting_comment),
        "selection": list(run.selection) if run else [],
    }
    employee.dialogue = {**employee.dialogue, "pending_intent": state}
    send(
        session,
        employee.identity_id,
        "Как использовать это сообщение?\n\nПока оно не записано в ответ опроса и не отправлено боту или HR.",
        "intent:" + key,
        now(session),
        buttons=[
            {"text": "Это ответ / комментарий к опросу", "payload": f"intent:{token}:answer"},
            {"text": "Отдельный вопрос ИИ", "payload": f"intent:{token}:ask"},
            {"text": "Сообщение HR", "payload": f"intent:{token}:hr"},
        ],
    )
    return True


def callback(runtime, session, employee, payload, key):
    if not payload.startswith("intent:"):
        return False
    from . import routes, surveys

    parts = payload.split(":")
    pending = employee.dialogue.get("pending_intent", {})
    run = surveys.active_run(session, employee)
    route = routes.active(session, employee) if not run else None
    current = run or route
    valid = (
        len(parts) == 3
        and pending.get("token") == parts[1]
        and current
        and pending.get("run_id") == current.id
        and pending.get("position") == current.position
        and (
            not run
            or pending.get("awaiting_comment") == run.awaiting_comment
            and pending.get("selection") == run.selection
        )
    )
    if not valid:
        send(
            session,
            employee.identity_id,
            "Этот выбор уже неактуален.\n\nПродолжить текущий вопрос: /resume.",
            "intent-stale:" + key,
            now(session),
        )
        return True
    employee.dialogue = {k: v for k, v in employee.dialogue.items() if k != "pending_intent"}
    action = parts[2]
    if action == "answer":
        if run:
            surveys.consume_answer(session, employee, run, pending["text"], key)
        else:
            routes.consume(session, employee, pending["text"], key)
    elif action == "ask":
        from .bot import ask

        ask(runtime, session, employee, pending["text"], key, run)
    elif action == "hr":
        from .support import choose_recipient

        choose_recipient(
            session,
            employee,
            key,
            text=pending["text"],
            context={
                "survey_run_id": run.id if run else None,
                "route_run_id": route.id if route else None,
                "question_index": current.position,
            },
        )
    return True
