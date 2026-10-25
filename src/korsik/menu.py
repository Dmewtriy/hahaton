"""One action registry for slash commands, native callback menu and Mini App."""

from dataclasses import dataclass

from .access import can_use_workspace, is_admin, is_hr
from .calendar import now
from .queue import send


@dataclass(frozen=True)
class Action:
    id: str
    label: str
    description: str
    audience: str = "employee"
    optional: bool = False


ACTIONS = (
    Action("menu", "Меню", "Показать доступные действия", "all"),
    Action("ask", "Задать вопрос", "Вопрос по документам компании"),
    Action("hr", "Связаться с HR", "Написать HR или продолжить обращение"),
    Action("resume", "Продолжить опрос", "Показать текущий вопрос"),
    Action("cases", "Мои обращения", "Обращения HR и их состояние"),
    Action("food", "Где пообедать", "Питание на вашей площадке"),
    Action("places", "Места рядом", "Поиск на карте рядом с рабочим адресом"),
    Action("materials", "Полезные материалы", "Материалы вашей площадки"),
    Action("plan", "Адаптационный план", "Получить свой персональный план", optional=True),
    Action("events", "Мероприятия", "Доступные корпоративные мероприятия", optional=True),
    Action("cancel", "Отменить ввод", "Отменить ввод сообщения HR или вопроса к ИИ"),
    Action("admin", "Кабинет", "Открыть рабочее пространство", "admin"),
    Action("dashboard", "Сводка", "Статус команды и адаптации", "workspace"),
    Action("reply", "Ответить на обращение", "Помощь по адресному ответу HR", "hr"),
    Action("close", "Предложить решение", "Помощь по решению обращения", "hr"),
    Action("help", "Помощь", "Показать помощь и доступные действия", "all"),
    Action("start", "Приветствие", "Начать работу или подключиться по приглашению", "all"),
)


def available(runtime, actor, employee):
    active_employee = bool(
        employee and employee.active and employee.status == "active" and "employee" in actor.roles
    )
    return [
        a
        for a in ACTIONS
        if actor.active
        and (
            a.audience == "all"
            or a.audience == "employee"
            and active_employee
            or a.audience == "workspace"
            and can_use_workspace(actor)
            or a.audience == "hr"
            and is_hr(actor)
            or a.audience == "admin"
            and is_admin(actor)
        )
        and (not a.optional or runtime.settings.enable_optional)
        and (a.id != "dashboard" or bool(set(actor.roles) & {"hr", "hr_head", "manager"}))
    ]


def show(runtime, session, actor, employee, key, page=0):
    choices = [a for a in available(runtime, actor, employee) if a.id not in {"menu", "help"}]
    page = max(0, min(page, max(0, (len(choices) - 1) // 6)))
    buttons = [{"text": a.label, "payload": "nav:do:" + a.id} for a in choices[page * 6 : page * 6 + 6]]
    if page:
        buttons.append({"text": "← Назад", "payload": f"nav:page:{page - 1}"})
    if (page + 1) * 6 < len(choices):
        buttons.append({"text": "Далее →", "payload": f"nav:page:{page + 1}"})
    if page:
        buttons.append({"text": "Главная", "payload": "nav:page:0"})
    # Постоянная кнопка меню внизу списка действий (как закреплённый пункт в мессенджере).
    if not any(b.get("payload") == "nav:page:0" and b.get("text") == "Меню" for b in buttons):
        buttons.append({"text": "Меню", "payload": "nav:page:0"})
    send(
        session,
        actor.id,
        f"Меню Корсика · страница {page + 1}\n\n"
        "Выберите действие кнопкой ниже.\n"
        "Прогресс опроса при этом сохранится.",
        "menu:" + key,
        now(session),
        buttons=buttons,
    )


def resolve(runtime, session, actor, employee, event, key):
    """Return handled flag and translated command; navigation never consumes a survey answer."""
    payload = event.get("payload") or ""
    text = event.get("text", "").strip()
    if event["kind"] == "callback" and payload.startswith("nav:"):
        parts = payload.split(":")
        if len(parts) == 3 and parts[1] == "page":
            try:
                page = int(parts[2])
            except ValueError:
                page = 0
            show(runtime, session, actor, employee, key, page)
            return True, None
        action = next(
            (
                a
                for a in available(runtime, actor, employee)
                if len(parts) == 3 and parts[1] == "do" and a.id == parts[2]
            ),
            None,
        )
        if not action:
            send(
                session,
                actor.id,
                "Действие сейчас недоступно. Откройте меню заново.",
                "nav-denied:" + key,
                now(session),
            )
            return True, None
        if action.id in {"reply", "close"}:
            send(
                session,
                actor.id,
                "Откройте нужное обращение в кабинете и выберите действие. "
                + (
                    "Команда: /reply ID текст ответа."
                    if action.id == "reply"
                    else "Команда: /close ID текст решения."
                ),
                "nav-format:" + key,
                now(session),
            )
            return True, None
        return False, "/" + action.id
    if text.lower() in {"/menu", "/help", "/", "☰ меню", "меню"}:
        show(runtime, session, actor, employee, key)
        return True, None
    return False, None
