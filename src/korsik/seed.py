"""Reviewed, editable demo definitions; original XLSX is retained separately as source rows."""

import json

from sqlalchemy import select

from .access import new_token, token_hash
from .calendar import add_months, now
from .documents import store
from .employees import invite
from .models import Branch, Employee, Identity, Scenario, SurveyTemplate, uid
from .scenarios import rebuild_schedule
from .schemas import ScenarioDefinition


def option(code, label, *, comment=False, notify=False, reply=None):
    return {"id": code, "label": label, "needs_comment": comment, "notify_hr": notify, "reply": reply}


def rating(key, text):
    return {"id": key, "text": text, "kind": "rating", "minimum": 1, "maximum": 10, "comment_below": 8}


def definition():
    surveys = [
        {
            "id": "readiness",
            "title": "Перед первым рабочим днём",
            "questions": [
                {
                    "id": "ready",
                    "text": "Готовы к первому рабочему дню?",
                    "kind": "single",
                    "options": [
                        option("ready", "Да, готов"),
                        option("excited", "Жду с нетерпением"),
                        option(
                            "worried",
                            "Немного волнуюсь",
                            reply="Волноваться перед первым днём нормально. "
                            "Рекрутер и коллеги помогут освоиться.",
                        ),
                    ],
                }
            ],
        },
        {
            "id": "equipment",
            "title": "Рабочее место",
            "questions": [
                {
                    "id": "access",
                    "text": "Есть ли всё необходимое оборудование и доступы?",
                    "kind": "single",
                    "options": [
                        option("yes", "Да"),
                        option(
                            "no",
                            "Нет",
                            comment=True,
                            notify=True,
                            reply="Опишите, чего не хватает. Передадим информацию рекрутеру.",
                        ),
                    ],
                }
            ],
        },
        {
            "id": "day3",
            "title": "Первые три рабочих дня",
            "questions": [
                {
                    "id": "registration",
                    "text": "Были ли вопросы при оформлении?",
                    "kind": "single",
                    "options": [
                        option("ok", "Всё отлично"),
                        option("questions", "Остались вопросы", comment=True),
                        option("documents", "Трудности с документами", comment=True, notify=True),
                    ],
                },
                {
                    "id": "colleagues",
                    "text": "Как вас встретили коллеги?",
                    "kind": "single",
                    "options": [
                        option("great", "Отлично"),
                        option("looking", "Пока присматриваюсь"),
                        option("alone", "Все заняты", comment=True, notify=True),
                    ],
                },
                {
                    "id": "tasks",
                    "text": "Понятны ли задачи на период адаптации?",
                    "kind": "single",
                    "options": [
                        option("yes", "Да"),
                        option("waiting", "Жду разъяснений", comment=True),
                        option("no", "Пока непонятны", comment=True, notify=True),
                    ],
                },
            ],
        },
        {
            "id": "week2",
            "title": "Две недели работы",
            "questions": [
                {
                    "id": "tasks",
                    "text": "Понятны ли поставленные задачи?",
                    "kind": "single",
                    "options": [option("yes", "Да"), option("no", "Нет", comment=True)],
                },
                {
                    "id": "information",
                    "text": "Достаточно ли информации для качественной работы?",
                    "kind": "single",
                    "options": [option("yes", "Да"), option("no", "Нет", comment=True)],
                },
                rating("manager_available", "Насколько руководитель доступен для вопросов?"),
                rating("manager_feedback", "Оцените качество обратной связи руководителя."),
                rating("colleagues_support", "Оцените поддержку коллег."),
                rating("equipment_support", "Оцените помощь с техникой и рабочим местом."),
                rating("access_timeliness", "Насколько своевременно предоставили доступы?"),
                rating("hr_support", "Оцените поддержку HR по возникающим вопросам."),
                rating("hiring_support", "Оцените поддержку HR при подготовке к трудоустройству."),
            ],
        },
        {
            "id": "month1",
            "title": "Первый месяц",
            "questions": [
                {
                    "id": "plan",
                    "text": "Как продвигается работа по адаптационному плану?",
                    "kind": "single",
                    "options": [
                        option("good", "По плану"),
                        option("slightly_late", "Немного отстаю"),
                        option("late", "Значительно отстаю", comment=True, notify=True),
                    ],
                }
            ],
        },
        {
            "id": "month2",
            "title": "Впечатления после двух месяцев",
            "questions": [
                {
                    "id": "mood",
                    "text": "С каким настроением приходите на работу?",
                    "kind": "single",
                    "options": [
                        option("great", "Мне всё нравится"),
                        option("indifferent", "Безразлично", comment=True),
                        option("tired", "Устал от объёма задач", comment=True, notify=True),
                    ],
                },
                {
                    "id": "helpers",
                    "text": "Кто помогает в период адаптации?",
                    "kind": "multiple",
                    "options": [
                        option("manager", "Руководитель"),
                        option("colleagues", "Коллеги"),
                        option("recruiter", "Рекрутер"),
                        option("nobody", "Никто", comment=True, notify=True),
                    ],
                },
                {
                    "id": "improvement",
                    "text": "Что помогло бы ускорить адаптацию?",
                    "kind": "single",
                    "options": [
                        option("clear_tasks", "Более чёткие задачи"),
                        option("automation", "Автоматизация"),
                        option("help", "Помощь коллег"),
                        option("other", "Свой вариант", comment=True),
                    ],
                },
            ],
        },
        {
            "id": "final",
            "title": "Итоги адаптации",
            "questions": [
                {
                    "id": "difficulty",
                    "text": "Что было самым сложным?",
                    "kind": "single",
                    "options": [
                        option("ok", "Со всем справился"),
                        option("communications", "Коммуникации с отделами"),
                        option("tasks", "Нечёткие задачи"),
                        option("other", "Другое", comment=True),
                    ],
                },
                {
                    "id": "stay",
                    "text": "Планируете продолжить работу?",
                    "kind": "single",
                    "options": [
                        option("yes", "Да"),
                        option("unsure", "Не уверен", comment=True, notify=True),
                        option("no", "Задумываюсь об уходе", comment=True, notify=True),
                    ],
                },
                rating("workload", "Насколько комфортна нагрузка?"),
                rating("expectations", "Насколько работа соответствует ожиданиям?"),
                {
                    "id": "information",
                    "text": "Достаточно ли информации для работы?",
                    "kind": "single",
                    "options": [option("yes", "Да"), option("no", "Нет", comment=True)],
                },
                rating("manager_available", "Насколько руководитель доступен для вопросов?"),
                rating("manager_feedback", "Оцените качество обратной связи руководителя."),
                rating("colleagues_support", "Оцените поддержку коллег."),
                rating("departments", "Оцените взаимодействие с другими подразделениями."),
                rating("hr_support", "Оцените поддержку HR."),
                rating("goals", "Насколько удалось выполнить задачи периода адаптации?"),
                {"id": "suggestion", "text": "Что улучшить для более комфортной адаптации?", "kind": "text"},
            ],
        },
    ]
    steps = []

    def step(
        key,
        text,
        *,
        days=0,
        months=0,
        at="09:00",
        working_day=None,
        survey=None,
        resources=None,
        manual=False,
        categories=None,
        branches=None,
        kind="message",
        reply_to="none",
        anchor="hire",
    ):
        steps.append(
            {
                "id": key,
                "text": text,
                "kind": "survey" if survey else kind,
                "reply_to": reply_to,
                "schedule": {
                    "anchor": anchor,
                    "days": days,
                    "months": months,
                    "at": at,
                    "working_day": working_day,
                },
                "survey_id": survey,
                "resource_keys": resources or [],
                "manual": manual,
                "categories": categories or [],
                "branch_codes": branches or [],
            }
        )

    step(
        "preboarding",
        "Здравствуйте, {name}! Я Корсик, ваш помощник на период адаптации. "
        "Я присылаю материалы, провожу опросы и помогаю связаться с HR. Выберите удобный формат общения.",
        days=-3,
    )
    step(
        "employment_documents",
        "Подготовьте документы к первому рабочему дню. "
        "Перечень зависит от условий оформления; уточните его у рекрутера.",
        days=-3,
        at="09:05",
        resources=["employment_documents"],
    )
    step("transport", "Как добраться до площадки {branch}:", days=-3, at="09:10", resources=["transport"])
    step(
        "company_facts",
        "Мы создаём инфраструктуру для жизни и бизнеса. Познакомьтесь с материалами о компании.",
        days=-3,
        at="09:15",
        resources=["company"],
    )
    step(
        "tomorrow",
        "Завтра ваш первый рабочий день. Напоминаю подготовить документы.",
        days=-1,
        survey="readiness",
        resources=["employment_documents"],
    )
    step(
        "welcome",
        "{name}, поздравляю с первым рабочим днём! Коллеги и рекрутер помогут освоиться.",
        at="10:00",
        working_day=1,
    )
    step(
        "welcome_letter",
        "Добро пожаловать в команду. Материалы для начала работы:",
        at="10:05",
        working_day=1,
        resources=["welcome_letter"],
    )
    step("map", "Карта территории или офиса:", at="11:00", working_day=1, resources=["map"])
    step("equipment", "Проверим готовность рабочего места.", at="12:00", working_day=1, survey="equipment")
    step(
        "lunch",
        "Информация о питании. К ней можно вернуться командой /food.",
        at="12:30",
        working_day=1,
        resources=["food"],
    )
    step(
        "contacts",
        "Справочник коллег может требовать корпоративную сеть и учётную запись.",
        at="13:00",
        working_day=1,
        resources=["contacts"],
    )
    step(
        "presentation",
        "Вы — часть команды. Предлагаю познакомиться с компанией.",
        at="15:00",
        working_day=1,
        resources=["company"],
    )
    step(
        "first_day_end",
        "Первый рабочий день позади. Если нужна помощь, отправьте /hr.",
        at="18:00",
        working_day=1,
        categories=["office"],
    )
    step(
        "introduction",
        "Расскажите рекрутеру о своём опыте и навыках для представления коллегам.",
        reply_to="hr",
        at="10:00",
        working_day=2,
        manual=True,
    )
    step(
        "adaptation_plan",
        "Руководитель подготовил адаптационный план. Уточните задачи и ожидаемые результаты.",
        working_day=3,
        manual=True,
        categories=["office"],
        resources=["adaptation_plan"],
    )
    step(
        "sports",
        "Информация о спортивном комплексе:",
        at="11:00",
        working_day=3,
        branches=["GTP"],
        resources=["sports"],
    )
    step("day3", "Поделитесь впечатлениями о первых днях.", at="17:30", working_day=3, survey="day3")
    step(
        "events",
        "Как проходят корпоративные мероприятия:",
        days=10,
        branches=["GrPP", "Luzhniki"],
        resources=["events"],
    )
    step("week2", "Прошло две недели. Проверим, достаточно ли поддержки.", days=14, survey="week2")
    step("month1", "Поздравляю с первым месяцем работы! Как идёт адаптация?", months=1, survey="month1")
    step(
        "meeting",
        "В ближайшие дни запланируйте встречу с рекрутером по адаптационному плану. "
        "Точное время согласует рекрутер.",
        months=1,
        days=14,
    )
    step(
        "manager_meeting",
        "Проведите встречу с новым сотрудником {name} из {department}.",
        months=1,
        days=14,
        kind="manager_reminder",
    )
    step(
        "movies",
        "Подборка фильмов о начале работы и профессиональном развитии:",
        months=1,
        days=14,
        at="17:00",
        resources=["movies"],
    )
    step("month2_survey", "Поделитесь впечатлениями об адаптации.", months=1, days=21, survey="month2")
    step("third_month", "Начался третий месяц работы. До завершения адаптации осталось немного.", months=2)
    step(
        "books",
        "Книги для профессионального развития:",
        months=2,
        days=14,
        categories=["office"],
        resources=["books"],
    )
    step(
        "final",
        "Период адаптации подходит к концу. Поделитесь итоговыми впечатлениями.",
        anchor="probation_end",
        survey="final",
    )
    step(
        "farewell",
        "Спасибо за обратную связь! При необходимости вы можете обратиться к HR.",
        anchor="probation_end",
        at="09:10",
        resources=["playlist"],
    )
    return ScenarioDefinition.model_validate(
        {"name": "Корсик — адаптация", "version": 1, "steps": steps, "surveys": surveys}
    ).model_dump(mode="json")


def seed_demo(runtime):
    if not runtime.settings.enable_demo or runtime.settings.environment == "production":
        raise ValueError("Demo seed requires KORSIK_ENABLE_DEMO=true in development")
    with runtime.db.transaction(write_lock=True) as session:
        if session.scalar(select(Scenario.id).where(Scenario.name == "Корсик — адаптация")):
            raise ValueError("Demo already exists; no data were overwritten")
        data = definition()
        scenario = Scenario(name=data["name"], version=1, definition=data, branch_codes=["*"])
        session.add(scenario)
        session.flush()
        for code in ["GrPP", "Luzhniki", "GTP"]:
            session.add(
                Branch(
                    code=code,
                    name=code + " (демо)",
                    timezone="Europe/Moscow",
                    scenario_id=scenario.id,
                    resources={},
                )
            )
        credentials = {}
        actors = {}
        for name, role, max_id in [
            ("Рекрутер", "hr", "demo-hr"),
            ("Руководитель", "manager", "demo-manager"),
            ("Руководитель HR", "hr_head", "demo-head"),
        ]:
            token = new_token()
            actor = Identity(
                name=name + " (демо)",
                roles=[role],
                branch_codes=["*"],
                max_user_id=max_id,
                token_hash=token_hash(token),
            )
            session.add(actor)
            session.flush()
            actors[role] = actor
            credentials[role] = {"id": actor.id, "api_token": token, "max_user_id": max_id}
        identity = Identity(name="Новый сотрудник (демо)", roles=["employee"], branch_codes=["GrPP"])
        session.add(identity)
        session.flush()
        today = now(session).astimezone(__import__("zoneinfo").ZoneInfo("Europe/Moscow")).date()
        employee = Employee(
            identity_id=identity.id,
            name=identity.name,
            branch_code="GrPP",
            department="Демонстрационное подразделение",
            hire_date=today,
            probation_end=add_months(today, 3),
            timezone="Europe/Moscow",
            recruiter_id=actors["hr"].id,
            manager_id=actors["manager"].id,
        )
        session.add(employee)
        session.flush()
        from .admin_service import ensure_department

        employee.department_id = ensure_department(session, employee.department, employee.branch_code).id
        for survey in data["surveys"]:
            template_id = uid()
            session.add(
                SurveyTemplate(
                    id=template_id,
                    title=survey["title"],
                    description="Опрос адаптации",
                    definition={**survey, "id": template_id},
                    branch_codes=["*"],
                    created_by=actors["hr"].id,
                )
            )
        rebuild_schedule(session, employee)
        invitation = invite(session, identity.id, runtime.settings.invitation_hours)
        doc = store(
            session,
            runtime.settings,
            "ДЕМО: учебная инструкция по порталу",
            "demo_portal.txt",
            "Это демонстрационный документ для проверки интеграции ML. Не является регламентом компании.".encode(),
            ["GrPP"],
            [],
        )
        credentials["employee"] = {
            "id": employee.id,
            "identity_id": identity.id,
            "invitation_token": invitation,
            "max_user_id": "demo-employee",
            "hire_date": today.isoformat(),
        }
        credentials["scenario_id"] = scenario.id
        credentials["demo_document_id"] = doc.id
    path = runtime.settings.data_dir / "demo_credentials.json"
    path.write_text(json.dumps(credentials, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
