from sqlalchemy import select

from . import documents, employees, inbox, surveys, tickets
from .access import can_support, can_use_workspace, is_admin, is_hr
from .calendar import now
from .ml import MLUnavailable
from .models import ConversationMessage, Employee, Identity, Outbox, QuestionLog, ScheduledStep, Ticket
from .queue import send
from .scenarios import drain_dialogue_queue

MENU = (
    "Я Корсик. /menu — меню, /ask вопрос — спросить по документам, /hr текст — сообщение HR, "
    "/resume — продолжить опрос, /cases — мои обращения, /food — питание, /places запрос — места рядом с работой, /materials — материалы."
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
    if employee:
        from .onboarding import sync

        sync(session, employee)
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
    from . import helpflow, intent, routes, support

    if callback and (
        intent.callback(runtime, session, employee, callback, key)
        or support.callback(session, employee, callback, key)
        or helpflow.callback(runtime, session, employee, callback, key)
    ):
        return
    if callback and callback.startswith("r:"):
        if not routes.consume(session, employee, "", key, callback):
            send(
                session, identity.id, "Это действие устарело. Продолжить: /resume.", "route-stale:" + key, at
            )
        return
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
    if command in {"/ask", "/hr", "/cancel", "/resume", "/cases"}:
        employee.dialogue = {k: v for k, v in employee.dialogue.items() if k != "pending_intent"}
    if command == "/ask":
        employee.dialogue = {
            k: v for k, v in employee.dialogue.items() if k not in {"support_compose", "active_help"}
        }
    if command == "/cases":
        support.list_cases(session, employee, key)
    elif command == "/hr":
        detail = text.partition(" ")[2].strip()
        pending = employee.dialogue.get("support_compose", {})
        if detail:
            opened = list(
                session.scalars(
                    select(Ticket).where(Ticket.employee_id == employee.id, Ticket.status != "closed")
                )
            )
            if not pending.get("ticket_id") and not pending.get("force_new") and len(opened) > 1:
                support.list_cases(session, employee, key, detail)
            else:
                if len(opened) == 1 and not pending.get("ticket_id") and not pending.get("force_new"):
                    employee.dialogue = {
                        **employee.dialogue,
                        "support_compose": {
                            "ticket_id": opened[0].id,
                            "recipient_id": opened[0].recruiter_id,
                        },
                    }
                support.submit(session, employee, detail, key)
        else:
            support.choose_recipient(session, employee, key)
    elif command == "/cancel":
        employee.dialogue = {
            k: v
            for k, v in employee.dialogue.items()
            if k not in {"awaiting_hr_message", "awaiting_ai_question", "support_compose", "active_help"}
        }
        send(session, identity.id, "Ввод сообщения отменён.", "cancel-compose:" + key, at)
        if run:
            surveys.prompt(session, employee, run, "cancel:" + key)
    elif command in {"/menu", "/help", "/"}:
        send(session, identity.id, MENU, f"menu:{key}", at, buttons=admin_buttons(runtime, identity))
    elif command in {"/food", "/materials"}:
        from fastapi import HTTPException

        from .models import Branch, Material

        branch = session.get(Branch, employee.branch_code)
        if command == "/food":
            keys = [f"RES-R{r:03}:{branch.code}" for r in [26, 27, 28]] + [
                f"RES-R{r:03}" for r in [26, 27, 28]
            ]
            selected = next(
                (
                    m
                    for k in keys
                    if (m := session.get(Material, k))
                    and ("*" in m.branch_codes or branch.code in m.branch_codes)
                ),
                None,
            )
            if selected:
                try:
                    value = routes.resolve_material(session, employee, selected.key)
                    item = send(
                        session,
                        identity.id,
                        "Питание на вашей площадке" + ("\n" + value["text"] if value["text"] else ""),
                        f"food:{key}",
                        at,
                        document_ids=value["document_ids"],
                    )
                    item.body = {**item.body, "required_document_ids": value["document_ids"]}
                except HTTPException:
                    send(
                        session,
                        identity.id,
                        "PDF с информацией о питании пока не подготовлен. Обратитесь к рекрутеру.",
                        f"food-missing:{key}",
                        at,
                    )
                return
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
    elif command == "/places":
        from .models import Branch
        from .places import search_link

        result = search_link(
            session.get(Branch, employee.branch_code), text.partition(" ")[2].strip() or "кафе"
        )
        send(
            session,
            identity.id,
            result["message"],
            "places:" + key,
            at,
            buttons=[{"type": "link", "text": "Открыть поиск на карте", "url": result["url"]}]
            if result["url"]
            else [],
        )
    elif command == "/resume":
        employee.dialogue = {
            k: v
            for k, v in employee.dialogue.items()
            if k not in {"support_compose", "active_help", "awaiting_ai_question"}
        }
        if run:
            employee.dialogue = {k: v for k, v in employee.dialogue.items() if k != "awaiting_hr_message"}
            surveys.prompt(session, employee, run, key)
        else:
            drain_dialogue_queue(session, employee)
            if routes.active(session, employee):
                routes.consume(session, employee, "", key)
            elif not surveys.active_run(session, employee):
                blocked = session.scalar(
                    select(ScheduledStep).where(
                        ScheduledStep.employee_id == employee.id,
                        ScheduledStep.status.in_(["waiting_material", "late_review"]),
                    )
                )
                send(
                    session,
                    identity.id,
                    "Продолжение ожидает подготовки материалов или запуска HR."
                    if blocked
                    else "Активного опроса нет.",
                    f"resume:{key}",
                    at,
                )
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
    elif not command.startswith("/") and employee.dialogue.get("support_compose"):
        pending = employee.dialogue["support_compose"]
        if pending.get("recipient_id"):
            support.submit(session, employee, text, key)
        else:
            support.choose_recipient(
                session,
                employee,
                key,
                text=text,
                context=pending.get("context"),
                ticket_id=pending.get("ticket_id"),
            )
    elif not command.startswith("/") and helpflow.consume(runtime, session, employee, text, key):
        pass
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
        if not intent.offer(session, employee, text, key, run=run):
            surveys.consume_answer(session, employee, run, text, key)
    elif intent.offer(session, employee, text, key, route=routes.active(session, employee)):
        pass
    elif routes.consume(session, employee, text, key):
        pass
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
        k: v
        for k, v in employee.dialogue.items()
        if k not in {"awaiting_hr_message", "awaiting_ai_question", "support_compose", "active_help"}
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


def ask(runtime, session, employee, question, key, run, *, help_id=None):
    from . import helpflow

    help = helpflow.begin(session, employee, question, run, help_id)
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
        automatic_hr = status in {"not_found", "blocked"}
    except MLUnavailable:
        text, status, version, topics = (
            "Сервис ответов временно недоступен. Я не могу дать подтверждённый ответ.",
            "unavailable",
            "unavailable",
            [],
        )
        automatic_hr = True
    if automatic_hr:
        ticket = tickets.open_ticket(
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
            "help:" + help.id,
            human=True,
        )
        help.ticket_id = ticket.id
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
    helpflow.finish(session, employee, help, status, text, version)


def admin_buttons(runtime, actor):
    settings = runtime.settings
    if not can_use_workspace(actor) or not settings.webapp_public_url or not settings.max_bot_name:
        return []
    return [
        {
            "type": "link",
            "text": "Кабинет HR" if is_admin(actor) else "Моя команда",
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
    if command in {"/menu", "/help", "/"} and can_use_workspace(actor):
        send(
            session,
            actor.id,
            (
                "Команды HR: /admin — кабинет, /dashboard — сводка, /reply ID текст — ответ, /close ID решение — предложить решение сотруднику."
                if is_admin(actor)
                else "Моя команда: /dashboard — статус подчинённых и рекомендации личных встреч."
            ),
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
            report = (
                f"Моя команда\nСотрудников: {data['totals']['employees']}\n"
                f"Незавершённых опросов: {data['totals']['active_surveys']}\nСтатус сотрудников:\n"
            ) + "\n".join(
                f"{e['name']} — {e['stage_label']}; выход: {e['hire_date']}" for e in data["employees"]
            )
        send(
            session, actor.id, report, f"dashboard:{key}", now(session), buttons=admin_buttons(runtime, actor)
        )
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
        if answer.strip():
            from .support import resolution

            resolution(session, actor, ticket, answer.strip(), key)
        elif ticket.resolution:
            tickets.close(session, actor, employee, ticket, key)
        else:
            send(
                session,
                actor.id,
                "Укажите решение: /close ID текст решения. Обращение закроется после подтверждения сотрудником.",
                "close-help:" + key,
                now(session),
            )
    else:
        send(session, actor.id, "Формат: /reply ID текст ответа", f"reply-help:{key}", now(session))
    return True
