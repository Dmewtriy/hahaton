from datetime import timedelta

from sqlalchemy import select

from .calendar import now
from .models import ScheduledStep, SurveyAnswer, SurveyRun
from .queue import enqueue, send


def start_survey(session, employee, step):
    run = SurveyRun(
        employee_id=employee.id,
        scheduled_step_id=step.id,
        definition=step.payload["survey"],
        started_at=now(session),
    )
    session.add(run)
    session.flush()
    for day in sorted(set(run.definition.get("reminder_days", [1, 2]))):
        enqueue(
            session,
            "reminder",
            {"run_id": run.id},
            f"reminder:{run.id}:{day}",
            now(session) + timedelta(days=day),
        )
    prompt(session, employee, run, "start")


def active_run(session, employee):
    return session.scalar(
        select(SurveyRun)
        .where(
            SurveyRun.employee_id == employee.id,
            SurveyRun.status == "active",
        )
        .with_for_update()
    )


def prompt(session, employee, run, suffix):
    question = run.definition["questions"][run.position]
    buttons = []
    if run.awaiting_comment:
        text = question.get("comment_prompt", "Расскажите подробнее.")
    else:
        text = question["text"]
        if question["kind"] in {"single", "multiple"}:
            for option in question["options"]:
                mark = "✓ " if option["id"] in run.selection else ""
                buttons.append(
                    {"text": mark + option["label"], "payload": f"a:{run.id}:{run.position}:{option['id']}"}
                )
            text += "\n" + "\n".join(f"{o['id']}: {o['label']}" for o in question["options"])
            if question["kind"] == "multiple":
                text += "\nВыберите несколько вариантов и нажмите «Готово» или отправьте /done."
                buttons.append({"text": "Готово", "payload": f"a:{run.id}:{run.position}:done"})
        elif question["kind"] == "rating":
            text += f"\nВведите число от {question['minimum']} до {question['maximum']}."
    text += "\nДля отдельного вопроса: /ask ваш вопрос. Прогресс опроса сохранится."
    send(
        session,
        employee.identity_id,
        text,
        f"survey:{run.id}:{run.position}:{suffix}",
        now(session),
        buttons=buttons,
    )


def consume_answer(session, employee, run, text, event_key, callback=None):
    question = run.definition["questions"][run.position]
    if callback:
        parts = callback.split(":")
        if len(parts) != 4 or parts[0] != "a" or parts[1] != run.id or parts[2] != str(run.position):
            send(
                session,
                employee.identity_id,
                "Эта кнопка устарела. Используйте текущий вопрос.",
                f"stale:{event_key}",
                now(session),
            )
            return
        text = parts[3]
    answer = session.scalar(
        select(SurveyAnswer).where(
            SurveyAnswer.run_id == run.id,
            SurveyAnswer.question_id == question["id"],
        )
    )
    if run.awaiting_comment:
        if callback or not text.strip() or len(text) > 15000:
            prompt(session, employee, run, event_key)
            return
        answer.comment = text.strip()
        run.awaiting_comment = False
        queue_analysis(session, employee, answer, question)
        advance(session, employee, run, event_key)
        return

    kind = question["kind"]
    options = {o["id"]: o for o in question.get("options", [])}
    needs_comment = False
    notify_hr = False
    if kind in {"single", "multiple"}:
        if kind == "multiple" and text in {"done", "/done"}:
            if not run.selection:
                prompt(session, employee, run, event_key)
                return
            value = list(run.selection)
        elif text in options:
            if kind == "multiple":
                selection = set(run.selection)
                selection.symmetric_difference_update({text})
                run.selection = sorted(selection)
                prompt(session, employee, run, event_key)
                return
            value = text
        else:
            prompt(session, employee, run, event_key)
            return
        selected = value if isinstance(value, list) else [value]
        needs_comment = any(options[x].get("needs_comment") for x in selected)
        notify_hr = any(options[x].get("notify_hr") for x in selected)
        for option_id in selected:
            if options[option_id].get("reply"):
                send(
                    session,
                    employee.identity_id,
                    options[option_id]["reply"],
                    f"option:{event_key}:{option_id}",
                    now(session),
                )
    elif kind == "rating":
        try:
            value = int(text)
        except ValueError:
            value = None
        if value is None or not question["minimum"] <= value <= question["maximum"]:
            prompt(session, employee, run, event_key)
            return
        needs_comment = question.get("comment_below") is not None and value < question["comment_below"]
    else:
        if callback or not text.strip():
            prompt(session, employee, run, event_key)
            return
        value = text.strip()

    answer = SurveyAnswer(
        run_id=run.id,
        question_id=question["id"],
        value={"kind": kind, "answer": value},
        created_at=now(session),
    )
    session.add(answer)
    session.flush()
    if notify_hr:
        from .tickets import open_ticket

        open_ticket(
            session,
            employee,
            "Ответ в опросе требует внимания",
            {"answer_id": answer.id},
            f"answer:{answer.id}",
            human=False,
        )
    if needs_comment:
        run.awaiting_comment = True
        prompt(session, employee, run, event_key)
    else:
        if kind == "text":
            queue_analysis(session, employee, answer, question)
        advance(session, employee, run, event_key)


def queue_analysis(session, employee, answer, question):
    enqueue(
        session,
        "analyze",
        {"answer_id": answer.id, "employee_id": employee.id, "question": question["text"]},
        f"analyze:{answer.id}",
        now(session),
    )


def advance(session, employee, run, event_key):
    run.selection = []
    run.position += 1
    if run.position == len(run.definition["questions"]):
        run.status = "completed"
        run.completed_at = now(session)
        session.get(ScheduledStep, run.scheduled_step_id).status = "completed"
        send(
            session,
            employee.identity_id,
            "Спасибо за обратную связь! Опрос завершён.",
            f"complete:{run.id}",
            now(session),
        )
        session.flush()
        from .scenarios import drain_dialogue_queue

        drain_dialogue_queue(session, employee)
    else:
        prompt(session, employee, run, event_key)
