from sqlalchemy import select

from . import documents, employees, inbox, surveys, tickets
from .access import can_support, is_admin, is_hr
from .calendar import now
from .ml import MLUnavailable
from .models import ConversationMessage, Employee, Identity, Outbox, QuestionLog, ScheduledStep, Ticket
from .queue import send
from .scenarios import drain_dialogue_queue

MENU = (
    "Я Корсик. /menu — меню, /ask вопрос — спросить по документам, /hr текст — сообщение HR, "
    "/resume — продолжить опрос, /food — питание, /materials — материалы."
)


def receive(runtime, session, event):
    key = event["event_id"]
    at = now(session)
    identity = session.scalar(select(Identity).where(Identity.max_user_id == event["max_user_id"]))
    text = event.get("text", "").strip()
    token = event.get("payload") if event["kind"] == "start" else None
    if text.startswith("/start "):
        token = text.split(maxsplit=1)[1]
    if token:
        bound = employees.bind(session, token, event["max_user_id"])
        if bound:
            identity = bound
    if not identity:
        session.add(
            Outbox(
                target_user_id=event["max_user_id"],
                dedupe_key=f"unknown:{key}",
                body={
                    "text": "Для подключения нужно действующее приглашение от HR. "
                    "Откройте ссылку или отправьте /start код_приглашения.",
                    "buttons": [],
                    "document_ids": [],
                },
                retry_at=at,
                created_at=at,
            )
        )
        return
    if not identity.active:
        return
    employee = session.scalar(select(Employee).where(Employee.identity_id == identity.id).with_for_update())
    if event["kind"] == "stop":
        if employee:
            employee.status = "stopped"
        return
    if event["kind"] == "start" or text.startswith("/start"):
        if employee and employee.active:
            employee.status = "active"
        buttons = (
            [
                {"text": "На ты", "payload": "style:informal"},
                {"text": "На вы", "payload": "style:formal"},
            ]
            if employee
            else []
        )
        buttons.extend(admin_buttons(runtime, identity))
        send(
            session,
            identity.id,
            f"Здравствуйте, {identity.name}! " + MENU,
            f"welcome:{key}",
            at,
            buttons=buttons,
        )
        if employee:
            drain_dialogue_queue(session, employee)
        return
    if staff_command(runtime, session, identity, text, key):
        return
    if not employee or not employee.active or employee.status != "active" or "employee" not in identity.roles:
        send(
            session,
            identity.id,
            "Используйте /dashboard. Рекрутер: /reply ID текст, /close ID.",
            f"staff-help:{key}",
            at,
        )
        return
    callback = event.get("payload") if event["kind"] == "callback" else None
    if callback and callback.startswith("style:"):
        style = callback.split(":", 1)[1]
        if style in {"formal", "informal"}:
            employee.address_style = style
            send(session, identity.id, "Формат общения сохранён.", f"style:{key}", at)
        return
    if callback == "hr":
        text = "/hr"
        callback = None
    run = surveys.active_run(session, employee)
    if callback:
        if run:
            employee.dialogue = {k: v for k, v in employee.dialogue.items() if k != "awaiting_hr_message"}
            surveys.consume_answer(session, employee, run, "", key, callback=callback)
        else:
            send(session, identity.id, "Эта кнопка больше не активна.", f"inactive-button:{key}", at)
        return
    command = text.split(maxsplit=1)[0].lower() if text else ""
    if command == "/hr":
        detail = text.partition(" ")[2].strip()
        if detail:
            route_hr_message(session, employee, detail, key, run)
        else:
            pending = employee.dialogue.get("awaiting_hr_message", {})
            employee.dialogue = {
                **employee.dialogue,
                "awaiting_ai_question": False,
                "awaiting_hr_message": {
                    "step_id": pending.get("step_id"),
                    "survey_run_id": run.id if run else None,
                },
            }
            send(
                session,
                identity.id,
                "Напишите одно сообщение для HR. Оно не попадёт в ИИ или ответы опроса. "
                "Для отмены: /cancel. После отправки бот вернётся к обычной работе.",
                "hr-compose:" + key,
                at,
            )
    elif command == "/cancel":
        employee.dialogue = {
            k: v
            for k, v in employee.dialogue.items()
            if k not in {"awaiting_hr_message", "awaiting_ai_question"}
        }
        send(session, identity.id, "Ввод сообщения отменён.", "cancel-compose:" + key, at)
        if run:
            surveys.prompt(session, employee, run, "cancel:" + key)
    elif command in {"/menu", "/help", "/"}:
        send(session, identity.id, MENU, f"menu:{key}", at, buttons=admin_buttons(runtime, identity))
    elif command in {"/food", "/materials"}:
        from .models import Branch

        branch = session.get(Branch, employee.branch_code)
        material = (
            branch.resources.get("food") if command == "/food" else "\n".join(branch.resources.values())
        )
        send(
            session,
            identity.id,
            material or "Материал пока не загружен. Обратитесь к рекрутеру.",
            f"material:{key}",
            at,
        )
    elif command == "/resume":
        if run:
            employee.dialogue = {k: v for k, v in employee.dialogue.items() if k != "awaiting_hr_message"}
            surveys.prompt(session, employee, run, key)
        else:
            drain_dialogue_queue(session, employee)
            send(session, identity.id, "Активного опроса нет.", f"resume:{key}", at)
    elif command == "/ask":
        employee.dialogue = {k: v for k, v in employee.dialogue.items() if k != "awaiting_hr_message"}
        employee.dialogue = {**employee.dialogue, "awaiting_ai_question": False}
        question = text.partition(" ")[2].strip()
        if question:
            ask(runtime, session, employee, question, key, run)
        else:
            employee.dialogue = {**employee.dialogue, "awaiting_ai_question": True}
            send(
                session,
                identity.id,
                "Напишите вопрос. Точка возврата в опрос сохранена.",
                f"ask-prompt:{key}",
                at,
            )
    elif employee.dialogue.get("awaiting_hr_message") and not command.startswith("/"):
        pending = employee.dialogue["awaiting_hr_message"]
        if not run or pending.get("survey_run_id") == run.id:
            route_hr_message(session, employee, text, key, run)
        else:
            employee.dialogue = {k: v for k, v in employee.dialogue.items() if k != "awaiting_hr_message"}
            surveys.consume_answer(session, employee, run, text, key)
    elif employee.dialogue.get("awaiting_ai_question"):
        employee.dialogue = {**employee.dialogue, "awaiting_ai_question": False}
        ask(runtime, session, employee, text, key, run)
    elif run:
        surveys.consume_answer(session, employee, run, text, key)
    elif command.startswith("/"):
        send(
            session,
            identity.id,
            "Эта команда сейчас недоступна. Список команд: /menu.",
            "unknown-command:" + key,
            at,
        )
    elif text:
        ask(runtime, session, employee, text, key, None)


def route_hr_message(session, employee, text, key, run):
    if not text.strip():
        return
    pending = employee.dialogue.get("awaiting_hr_message", {})
    employee.dialogue = {
        k: v for k, v in employee.dialogue.items() if k not in {"awaiting_hr_message", "awaiting_ai_question"}
    }
    step = session.get(ScheduledStep, pending.get("step_id")) if pending.get("step_id") else None
    if step and step.employee_id == employee.id and step.status == "dispatched":
        step.status = "completed"
    inbox.send_employee_message(
        session, employee, text, key, "Ответ на шаг адаптации" if step else "Сообщение сотрудника"
    )
    send(
        session,
        employee.identity_id,
        "Сообщение передано HR. Для следующего сообщения используйте /hr. "
        "Опросы и рассылки продолжают работать.",
        "hr-ack:" + key,
        now(session),
    )
    if run:
        surveys.prompt(session, employee, run, "after-hr:" + key)


def ask(runtime, session, employee, question, key, run):
    allowed = documents.allowed_documents(session, employee)
    history = list(
        session.scalars(
            select(ConversationMessage)
            .where(ConversationMessage.employee_id == employee.id)
            .order_by(ConversationMessage.created_at.desc())
            .limit(8)
        )
    )
    payload = {
        "request_id": key,
        "employee_id": employee.id,
        "question": question,
        "history": [{"role": m.role, "text": m.text[:2000]} for m in reversed(history)],
        "step_context": {
            "stage": employee.stage,
            "survey_id": run.definition["id"] if run else None,
            "question_index": run.position if run else None,
        },
        "access": {"branch_code": employee.branch_code, "category": employee.category},
        "allowed_documents": [{"id": d.id, "title": d.title, "version": d.version} for d in allowed],
        "address_style": employee.address_style,
    }
    at = now(session)
    session.add(ConversationMessage(employee_id=employee.id, role="user", text=question, created_at=at))
    automatic_hr = False
    try:
        result = runtime.ml.answer(payload)
        # A document may have been revoked while the ML request was in flight.
        for doc in allowed:
            session.refresh(doc)
        by_id = {
            d.id: d
            for d in allowed
            if documents.employee_can_read(employee, d) and d.status in {"ready", "ready_stub"}
        }
        valid = bool(result.sources) and all(
            s.document_id in by_id and s.version == by_id[s.document_id].version for s in result.sources
        )
        if (result.status == "answered" and not valid) or (result.sources and not valid):
            text, status = (
                "Не удалось подтвердить ответ разрешёнными документами.",
                "blocked",
            )
        else:
            text, status = result.answer, result.status
            if status == "not_found":
                text = "В доступных документах я не нашёл подтверждённого ответа."
                if runtime.settings.ml_mode == "stub":
                    text += " Сейчас подключён демонстрационный ML-модуль."
            if result.status == "answered":
                text += "\nИсточники:\n" + "\n".join(
                    f"{by_id[s.document_id].title}, версия {s.version}"
                    + (f", стр. {s.page}" if s.page else "")
                    for s in result.sources
                )
        version, topics = result.processor_version, result.topics
        automatic_hr = result.needs_hr or status in {"not_found", "blocked"}
    except MLUnavailable:
        text, status, version, topics = (
            "Сервис ответов временно недоступен. Я не могу дать подтверждённый ответ.",
            "unavailable",
            "unavailable",
            [],
        )
        automatic_hr = True
    if automatic_hr:
        tickets.open_ticket(
            session,
            employee,
            "Вопрос без подтверждённого ответа",
            {
                "question": question,
                "answer_status": status,
                "processor_version": version,
                "history": [{"role": m.role, "text": m.text} for m in reversed(history)],
                "survey_run_id": run.id if run else None,
            },
            "rag:" + key,
            human=True,
        )
        text += (
            "\nПередал ваш вопрос закреплённому HR. Дополнить: /hr ваше сообщение. "
            "Опросы и рассылки продолжат приходить."
        )
    session.add(
        QuestionLog(
            employee_id=employee.id,
            request_key=key,
            status=status,
            topics=topics,
            processor_version=version,
            created_at=at,
        )
    )
    session.add(ConversationMessage(employee_id=employee.id, role="assistant", text=text, created_at=at))
    send(
        session,
        employee.identity_id,
        text,
        f"answer:{key}",
        at,
        buttons=[{"text": "Связаться с HR", "payload": "hr"}],
    )
    if run:
        surveys.prompt(session, employee, run, "return:" + key)


def admin_buttons(runtime, actor):
    settings = runtime.settings
    if not is_admin(actor) or not settings.webapp_public_url or not settings.max_bot_name:
        return []
    return [
        {
            "type": "link",
            "text": "Кабинет HR",
            "url": f"https://max.ru/{settings.max_bot_name.lstrip('@')}?startapp=admin",
        }
    ]


def staff_command(runtime, session, actor, text, key):
    command, _, rest = text.partition(" ")
    command = command.lower()
    if command == "/admin":
        if not is_admin(actor):
            send(
                session,
                actor.id,
                "Кабинет доступен только администраторам и HR.",
                "admin-denied:" + key,
                now(session),
            )
        else:
            buttons = admin_buttons(runtime, actor)
            text = (
                "Откройте кабинет управления."
                if buttons
                else "Мини-приложение пока не подключено. Кабинет доступен локально на сервере."
            )
            send(session, actor.id, text, "admin:" + key, now(session), buttons=buttons)
        return True
    if command in {"/menu", "/help"} and is_admin(actor):
        send(
            session,
            actor.id,
            "Команды HR: /admin — кабинет, /dashboard — сводка, "
            "/reply ID текст — ответ, /close ID — закрыть обращение.",
            "staff-menu:" + key,
            now(session),
            buttons=admin_buttons(runtime, actor),
        )
        return True
    if command == "/dashboard" and set(actor.roles) & {"hr", "manager", "hr_head"}:
        from .analytics import dashboard, digest_text

        data = dashboard(session, actor)
        report = digest_text(data)
        if "manager" in actor.roles and not is_hr(actor):
            report += "\nСтатус сотрудников:\n" + "\n".join(
                f"{e['name']} — {e['stage']}; выход: {e['hire_date']}" for e in data["employees"]
            )
        send(session, actor.id, report, f"dashboard:{key}", now(session))
        return True
    if command not in {"/reply", "/close"} or not is_hr(actor):
        return False
    ticket_id, _, answer = rest.partition(" ")
    ticket = session.get(Ticket, ticket_id)
    employee = session.get(Employee, ticket.employee_id) if ticket else None
    if not ticket or not employee or not can_support(actor, employee):
        send(session, actor.id, "Обращение недоступно.", f"ticket-denied:{key}", now(session))
    elif ticket.status == "closed":
        send(session, actor.id, "Обращение уже закрыто.", f"ticket-closed:{key}", now(session))
    elif command == "/reply" and answer.strip():
        tickets.reply(session, actor, employee, ticket, answer.strip(), key)
    elif command == "/close":
        tickets.close(session, actor, employee, ticket, key)
    else:
        send(session, actor.id, "Формат: /reply ID текст ответа", f"reply-help:{key}", now(session))
    return True
