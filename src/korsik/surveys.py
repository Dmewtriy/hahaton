from datetime import datetime, timedelta

from sqlalchemy import select

from .calendar import now
from .models import CampaignRecipient, ScheduledStep, SurveyAnswer, SurveyRun
from .queue import enqueue, send
from .templates import render


def start_survey(session, employee, step):
    employee.dialogue = {
        k: v for k, v in employee.dialogue.items() if k not in {"awaiting_hr_message", "support_compose"}
    }
    run = SurveyRun(
        employee_id=employee.id,
        scheduled_step_id=step.id,
        definition=step.payload["survey"],
        branch_code_at_start=employee.branch_code,
        data_mode=step.payload["survey"].get("data_mode", "real"),
        started_at=now(session),
        expires_at=datetime.fromisoformat(step.payload["expires_at"])
        if step.payload.get("expires_at")
        else None,
    )
    session.add(run)
    session.flush()
    for day in sorted(set(run.definition.get("reminder_days", [1, 2]))):
        enqueue(
            session,
            "reminder",
            {"run_id": run.id, "offset_days": day},
            f"reminder:{run.id}:{day}",
            run.started_at + timedelta(days=day),
        )
    prompt(session, employee, run, "start")


def active_run(session, employee):
    expire_runs(session, employee.id)
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
        if question.get("comment_contact_alternative"):
            text += " Можно объяснить причину здесь или связаться с HR."
            buttons = support_buttons(session, employee, run, question)
        if run.optional_comment:
            text = "Можно оставить комментарий или нажать «Продолжить»."
            buttons = [{"text": "Продолжить", "payload": f"a:{run.id}:{run.position}:skip"}]
    else:
        text = question["text"]
        if question["kind"] in {"single", "multiple"}:
            for option in question["options"]:
                mark = "✓ " if option["id"] in run.selection else ""
                buttons.append(
                    {"text": mark + option["label"], "payload": f"a:{run.id}:{run.position}:{option['id']}"}
                )
            text += "\n" + "\n".join(
                f"{index}. {'✓ ' if option['id'] in run.selection else ''}{option['label']}"
                for index, option in enumerate(question["options"], start=1)
            )
            if question["kind"] == "multiple":
                text += (
                    "\nВыберите варианты кнопками или отправляйте их номера по одному. "
                    "Затем нажмите «Готово» или напишите это слово."
                )
                buttons.append({"text": "Готово", "payload": f"a:{run.id}:{run.position}:done"})
            else:
                text += "\nНажмите вариант ответа или отправьте его номер."
        elif question["kind"] == "rating":
            text += f"\nВведите число от {question['minimum']} до {question['maximum']}."
            if question.get("minimum_label") or question.get("maximum_label"):
                text += f"\n{question['minimum']} — {question.get('minimum_label', '')}; {question['maximum']} — {question.get('maximum_label', '')}."
    text += (
        "\nДля отдельного вопроса: /ask ваш вопрос. Прогресс опроса сохранится. Сообщение HR: /hr ваш текст."
    )
    item = send(
        session,
        employee.identity_id,
        render(session, employee, text),
        f"survey:{run.id}:{run.position}:{suffix}",
        now(session),
        buttons=buttons,
    )
    from .models import RouteRun

    item.body = {
        **item.body,
        "buttons": item.body["buttons"] + [{"text": "☰ Меню", "payload": "nav:page:0"}],
    }

    route = session.scalar(select(RouteRun).where(RouteRun.scheduled_step_id == run.scheduled_step_id))
    if route:
        item.body = {**item.body, "route_run_id": route.id, "route_node": route.position}
    if item.status == "blocked_material":
        item.status = "pending"


def reminder_allowed(session, run_id):
    from .models import Employee, Outbox
    from .scenarios import step_allowed

    run = session.get(SurveyRun, run_id)
    if not run or run.status != "active" or run.expires_at and run.expires_at <= now(session):
        return False
    employee = session.get(Employee, run.employee_id)
    step = session.get(ScheduledStep, run.scheduled_step_id)
    initial = session.scalar(select(Outbox).where(Outbox.dedupe_key == f"survey:{run.id}:0:start"))
    return bool(
        employee
        and employee.active
        and employee.status == "active"
        and step
        and step_allowed(session, employee, step)
        and initial
        and initial.status == "sent"
    )


def consume_answer(session, employee, run, text, event_key, callback=None):
    if run.status != "active" or run.expires_at and run.expires_at <= now(session):
        expire_runs(session, employee.id)
        return
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
        if run.optional_comment and ((callback and text == "skip") or text.casefold() == "продолжить"):
            run.awaiting_comment = False
            run.optional_comment = False
            advance(session, employee, run, event_key)
            return
        if callback or not text.strip() or len(text) > 15000:
            prompt(session, employee, run, event_key)
            return
        answer.comment = text.strip()
        answer.comment_created_at = now(session)
        run.awaiting_comment = False
        run.optional_comment = False
        queue_analysis(session, employee, answer, question)
        advance(session, employee, run, event_key)
        return

    kind = question["kind"]
    options = {o["id"]: o for o in question.get("options", [])}
    needs_comment = False
    notify_hr = False
    if kind in {"single", "multiple"}:
        if not callback:
            entered = text.strip()
            if kind == "multiple" and entered.casefold() in {"готово", "done", "/done"}:
                text = "done"
            elif (
                entered.isascii()
                and entered.isdigit()
                and len(entered) <= 2
                and 1 <= int(entered) <= len(options)
            ):
                text = question["options"][int(entered) - 1]["id"]
            else:
                matching = [o["id"] for o in options.values() if o["label"].casefold() == entered.casefold()]
                text = matching[0] if len(matching) == 1 else entered
        if kind == "multiple" and text in {"done", "/done"}:
            if not run.selection:
                prompt(session, employee, run, event_key)
                return
            value = list(run.selection)
        elif text in options:
            if kind == "multiple":
                selection = set(run.selection)
                if options[text].get("exclusive"):
                    selection = set() if text in selection else {text}
                else:
                    selection = {x for x in selection if not options[x].get("exclusive")}
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
            if options[option_id].get("reply") and not options[option_id].get("reply_after_survey"):
                send(
                    session,
                    employee.identity_id,
                    render(session, employee, options[option_id]["reply"]),
                    f"option:{event_key}:{option_id}",
                    now(session),
                    buttons=support_buttons(session, employee, run, question)
                    if options[option_id].get("support_actions")
                    else [],
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
    elif kind == "rating" and question.get("optional_comment_on_positive"):
        run.awaiting_comment = True
        run.optional_comment = True
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
        if run.definition.get("kind") == "quiz":
            run.result = score_quiz(session, run)
        session.get(ScheduledStep, run.scheduled_step_id).status = "completed"
        recipient = session.scalar(
            select(CampaignRecipient).where(CampaignRecipient.scheduled_step_id == run.scheduled_step_id)
        )
        if recipient:
            recipient.status = "completed"
        completion_text = run.definition.get("completion_text", "Спасибо за обратную связь! Опрос завершён.")
        if run.result:
            completion_text = (
                f"Тест завершён. Результат: {run.result['points']} из {run.result['maximum']} "
                f"({run.result['percent']}%). "
                + (
                    "Тест пройден."
                    if run.result["passed"]
                    else "Порог пока не достигнут. Обратитесь к HR за повторным назначением."
                )
            )
        if completion_text:
            from .templates import render

            send(
                session,
                employee.identity_id,
                render(session, employee, completion_text),
                f"complete:{run.id}",
                now(session),
            )
        for question in run.definition["questions"]:
            answer = session.scalar(
                select(SurveyAnswer).where(
                    SurveyAnswer.run_id == run.id, SurveyAnswer.question_id == question["id"]
                )
            )
            selected = (
                answer.value["answer"]
                if isinstance(answer.value["answer"], list)
                else [answer.value["answer"]]
            )
            for option in question.get("options", []):
                if option["id"] in selected and option.get("reply_after_survey") and option.get("reply"):
                    send(
                        session,
                        employee.identity_id,
                        option["reply"],
                        f"survey-reaction:{run.id}:{question['id']}:{option['id']}",
                        now(session),
                        buttons=support_buttons(session, employee, run, question)
                        if option.get("support_actions")
                        else [],
                    )
        from .routes import survey_completed

        survey_completed(session, employee, run)
        session.flush()
        from .scenarios import drain_dialogue_queue

        drain_dialogue_queue(session, employee)
    else:
        prompt(session, employee, run, event_key)


def score_quiz(session, run):
    answers = {
        a.question_id: a.value["answer"]
        for a in session.scalars(select(SurveyAnswer).where(SurveyAnswer.run_id == run.id))
    }
    maximum = sum(q.get("points", 1) for q in run.definition["questions"])
    points = 0
    for question in run.definition["questions"]:
        value = answers.get(question["id"])
        selected = set(value if isinstance(value, list) else [value])
        if selected == set(question.get("correct_options", [])):
            points += question.get("points", 1)
    # Compare integer products: rounding must not turn a failed result into a pass.
    return {
        "points": points,
        "maximum": maximum,
        "percent": round(100 * points / maximum, 2),
        "pass_percent": run.definition.get("pass_percent", 70),
        "passed": 100 * points >= maximum * run.definition.get("pass_percent", 70),
    }


def expire_runs(session, employee_id=None):
    from .models import Employee
    from .scenarios import step_allowed

    query = select(SurveyRun).where(SurveyRun.status == "active")
    if employee_id:
        query = query.where(SurveyRun.employee_id == employee_id)
    for run in session.scalars(query):
        step = session.get(ScheduledStep, run.scheduled_step_id)
        expired = run.expires_at and run.expires_at <= now(session)
        if not expired and step_allowed(session, session.get(Employee, run.employee_id), step):
            continue
        run.status = "expired" if expired else "cancelled"
        step.status = run.status
        recipient = session.scalar(
            select(CampaignRecipient).where(CampaignRecipient.scheduled_step_id == run.scheduled_step_id)
        )
        if recipient:
            recipient.status = run.status


def support_buttons(session, employee, run, question):
    from .models import Identity

    hr = session.get(Identity, employee.recruiter_id)
    buttons = [{"text": "Передать HR через бота", "payload": f"support:{run.id}:{question['id']}"}]
    if hr and hr.contact_url:
        buttons.append(
            {"text": "Личный контакт рекрутера", "payload": f"contact:{run.id}:{question['id']}"}
            if question.get("comment_contact_alternative")
            else {"type": "link", "text": "Личный контакт рекрутера", "url": hr.contact_url}
        )
    return buttons
