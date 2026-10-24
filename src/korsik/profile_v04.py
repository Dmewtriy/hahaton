"""Deterministic compiler of the supplied Excel catalogue; no generated corporate content."""

import json
import re
from pathlib import Path

from sqlalchemy import select

from .models import Material, Scenario
from .schemas import ScenarioDefinition

BRANCHES = {
    9: ["GrPP", "Luzhniki"],
    10: ["GTP"],
    11: ["GrPP"],
    12: ["GTP"],
    13: ["Luzhniki"],
    20: ["GTP"],
    21: ["Luzhniki"],
    22: ["GrPP", "Luzhniki"],
    23: ["GTP"],
    26: ["GrPP"],
    27: ["Luzhniki"],
    28: ["GTP"],
    30: ["GrPP", "Luzhniki"],
    31: ["GTP"],
    37: ["GTP"],
}


def source(name):
    root = Path(__file__).resolve().parents[2] / "examples"
    if not root.exists():
        root = Path.cwd() / "examples"
    return json.loads((root / name).read_text(encoding="utf-8"))


def compile_profile():
    cells = source("v04_source_cells.json")["sheets"][0]["cells"]

    def raw(col, row):
        return str(cells.get(f"{col}{row}", {}).get("value") or "").strip()

    def text(row):
        return re.sub(r"(?<![А-Яа-я])Х(?=[!,.\s]|$)", "{name}", raw("F", row))

    def message(row, **extra):
        return {
            "id": f"r{row}",
            "source_ref": f"XLS1-R{row:03}",
            "kind": "message",
            "text": text(row),
            **extra,
        }

    def material(row, prefix="MAT", **extra):
        return {
            "id": f"{prefix.lower()}{row}",
            "source_ref": f"XLS1-R{row:03}",
            "kind": "material",
            "material_key": f"{prefix}-R{row:03}",
            "text": "",
            **extra,
        }

    def choice(id, prompt, options, **extra):
        return {
            "id": id,
            "kind": "choice",
            "text": prompt,
            "options": [{"id": str(i + 1), "label": label} for i, label in enumerate(options)],
            **extra,
        }

    def extract_question(row, qid):
        raw_text = text(row)
        head = re.split(r"\n\s*(?:Если|Сразу после)", raw_text, maxsplit=1)[0]
        options = re.findall(r"(?m)^\s*[А-ГA-D\d]+\)\s*(.+)$", head)
        if not options:
            options = re.findall(r"(?m)^\s*-\s*(.+)$", head)
        prompt = re.split(r"(?m)^\s*(?:[А-ГA-D\d]+\)|-\s)", head, maxsplit=1)[0].strip()
        prompt = re.sub(r"\s*Варианты ответов:\s*$", "", prompt)
        return {
            "id": qid,
            "text": prompt,
            "kind": "single",
            "source_ref": f"XLS1-R{row:03}",
            "options": [
                {"id": f"o{i + 1}", "label": re.split(r":\s*возможность", label, flags=re.I)[0].strip()}
                for i, label in enumerate(options)
            ],
        }

    def reaction(row, letter):
        match = re.search(
            r"Если ответ\s*" + letter + r":\s*(.*?)(?=\n\s*(?:Если ответ|Возможность|Опции|Далее переход)|$)",
            text(row),
            re.S,
        )
        result = match[1].strip() if match else ""
        return "" if result.startswith("Переход") else result

    steps = []
    surveys = []
    titles = [
        "Знакомство с Корсиком",
        "Документы для оформления",
        "Как добраться до работы",
        "Факты о компании",
        "Готовность к первому дню",
        "Первый день и ИТ-доступы",
        "Карта рабочего пространства",
        "Оборудование и доступы",
        "Питание на площадке",
        "Справочник контактов",
        "Корпоративная презентация",
        "Завершение первого дня",
        "Спортивный комплекс",
        "Третий рабочий день",
        "Две недели работы",
        "Первый месяц и план адаптации",
        "Подборка фильмов",
        "Настроение и поддержка",
        "Два месяца работы",
        "Книги и открытка",
        "Завершение адаптации",
    ]

    def step(m, rows, nodes, **schedule):
        steps.append(
            {
                "id": m,
                "kind": "flow",
                "text": titles[int(m[1:]) - 1],
                "source_refs": [f"XLS1-R{r:03}" for r in rows],
                "actions": nodes,
                "schedule": {"at": "09:00", **schedule},
            }
        )

    def survey_action(survey, **extra):
        surveys.append(survey)
        return {
            "id": "survey_" + survey["id"],
            "kind": "survey",
            "survey_id": survey["id"],
            "text": "",
            **extra,
        }

    def survey_def(id, title, questions, completion=""):
        return {
            "id": id,
            "title": title,
            "questions": questions,
            "reminder_days": [],
            "completion_text": completion,
        }

    intro8 = re.search(r"1\.\s*Вариант:\s*(.*?)(?=2\.\s*Вариант:|$)", text(8), re.S)[1].strip()
    style = choice("style", text(5), ["На ТЫ", "На ВЫ"], source_ref="XLS1-R006")
    for option, value in zip(style["options"], ["informal", "formal"]):
        option["set_style"] = value
    step(
        "M01",
        [3, 4, 5, 6, 7, 8],
        [
            message(3),
            material(3),
            message(4),
            material(4),
            style,
            message(7),
            {**message(8), "text": intro8},
            material(8),
        ],
        days=-3,
    )
    step(
        "M02",
        [9, 10],
        [
            node
            for r in [9, 10]
            for node in [
                message(r, branch_codes=BRANCHES[r]),
                {**message(r), "id": f"list{r}", "text": raw("J", r), "branch_codes": BRANCHES[r]},
                material(r, branch_codes=BRANCHES[r]),
            ]
        ],
        days=-3,
        at="09:05",
    )
    transport = []
    for r in [11, 12, 13]:
        node = choice(
            f"transport{r}",
            text(r),
            ["Показать", "Сейчас не нужно"],
            branch_codes=BRANCHES[r],
            source_ref=f"XLS1-R{r:03}",
        )
        transport.extend(
            [node, material(r, "RES", branch_codes=BRANCHES[r], when={"choice": node["id"], "equals": "1"})]
        )
    step("M03", [11, 12, 13], transport, days=-3, at="09:10")
    step("M04", [14, 15], [message(14), message(15)], days=-3, at="09:15")
    ready = extract_question(16, "ready")
    positive = text(16).split("Если ответили 1-2:")[1].split("Если ответили 3:")[0].strip()
    worried = text(16).split("Если ответили 3:")[1].split("Весь первый день")[0].strip()
    common = "Весь первый день" + text(16).split("Весь первый день", 1)[1]
    readiness = choice("ready", ready["text"], [o["label"] for o in ready["options"]], source_ref="XLS1-R016")
    for i, o in enumerate(readiness["options"]):
        o["reply"] = (positive if i < 2 else worried) + "\n\n" + common
    step(
        "M05",
        [16, 17],
        [readiness, message(17), {**message(17), "id": "list17", "text": raw("J", 17)}, material(17)],
        days=-1,
    )
    step(
        "M06",
        [18],
        [
            message(18),
            material(18),
            {
                "id": "it",
                "kind": "employee_resource",
                "field": "it_access_url",
                "title": "Подготовленные ИТ-доступы",
                "text": "Подготовленные ИТ-доступы:",
                "source_ref": "XLS1-R018",
            },
        ],
        working_day=1,
        at="10:00",
    )
    maps = []
    for r in [20, 21]:
        node = choice(
            f"map{r}",
            text(r),
            ["Показать", "Сейчас не нужно"],
            branch_codes=BRANCHES[r],
            source_ref=f"XLS1-R{r:03}",
        )
        maps.extend(
            [node, material(r, "RES", branch_codes=BRANCHES[r], when={"choice": node["id"], "equals": "1"})]
        )
    step("M07", [20, 21], maps, working_day=1, at="11:00")
    steps[-1]["branch_codes"] = ["GTP", "Luzhniki"]
    equipment = []
    for r in [22, 23]:
        q = extract_question(r, "access")
        node = choice(
            f"equipment{r}",
            q["text"],
            [o["label"] for o in q["options"]],
            branch_codes=BRANCHES[r],
            source_ref=f"XLS1-R{r:03}",
        )
        for option, marker in zip(node["options"], ["Если да:", "Если нет:"]):
            option["reply"] = text(r).split(marker)[1].split("Если нет:")[0].strip()
        equipment.append(node)
    step("M08", [22, 23], equipment, working_day=1, at="12:00")
    lunch = choice(
        "lunch",
        text(25).split("Варианты ответов:")[0].strip(),
        ["Да!", "Нет, спасибо!"],
        source_ref="XLS1-R025",
    )
    lunch["options"][1]["reply"] = text(25).split("Если нет:")[1].strip()
    lunch_nodes = [lunch]
    for r in [26, 27, 28]:
        cond = {"choice": "lunch", "equals": "1"}
        scope = BRANCHES[r]
        lunch_nodes += [
            material(r, "RES", branch_codes=scope, when=cond),
            material(r, branch_codes=scope, when=cond),
            {**message(r), "text": "Приятного аппетита!", "branch_codes": scope, "when": cond},
        ]
    step("M09", [25, 26, 27, 28], lunch_nodes, working_day=1, at="12:30")
    contacts = [message(29)]
    for r in [30, 31]:
        contacts += [
            {
                **message(r),
                "text": re.sub(r"# Приложить ссылку на справочник\s*", "", text(r)),
                "branch_codes": BRANCHES[r],
            },
            material(r, branch_codes=BRANCHES[r]),
        ]
    step("M10", [29, 30, 31], contacts, working_day=1, at="13:00")
    step("M11", [32], [message(32), material(32, "RES")], working_day=1, at="15:00")
    step("M12", [33], [message(33), material(33)], working_day=1, at="18:00")
    steps[-1]["categories"] = ["office"]
    sports = choice("sports", text(37), ["Показать", "Сейчас не нужно"], source_ref="XLS1-R037")
    step(
        "M13",
        [37],
        [sports, material(37, "RES", when={"choice": "sports", "equals": "1"})],
        working_day=3,
        at="11:00",
    )
    steps[-1]["branch_codes"] = ["GTP"]
    day3 = []
    for r in [39, 40, 41]:
        q = extract_question(r, f"D3Q{r - 38:02}")
        for i, letter in enumerate(["А", "Б", "В"]):
            reply = reaction(r, letter)
            if reply:
                q["options"][i].update(
                    reply=reply, support_actions=(r in {39, 41} and i > 0 or r == 40 and i == 2)
                )
        day3.append(q)
    step(
        "M14",
        [38, 39, 40, 41, 42],
        [message(38), survey_action(survey_def("M14", "Опрос третьего рабочего дня", day3, text(42)))],
        working_day=3,
        at="17:30",
    )
    week2 = []
    for item in source("v04_surveys.json")["M15"]:
        segment = item["raw_segment"]
        prompt = re.split(r"\(\s*(?:да/нет|шкала)|\b1\s*[-–]", segment, maxsplit=1, flags=re.I)[0].strip()
        q = {
            "id": item["id"],
            "text": prompt,
            "kind": "single" if item["type"] == "yes_no" else "rating",
            "source_ref": "XLS1-R047",
        }
        if q["kind"] == "single":
            q["options"] = [{"id": "yes", "label": "Да"}, {"id": "no", "label": "Нет", "needs_comment": True}]
        else:
            q.update(
                minimum=1,
                maximum=10,
                comment_below=8,
                optional_comment_on_positive=item["optional_comment_on_positive"],
            )
            labels = re.search(r"\b1\s*[-–]\s*(.*?),\s*10\s*[-–]\s*([^\n)+]+)", segment)
            if labels:
                q.update(minimum_label=labels[1].strip(), maximum_label=labels[2].strip(" .,;"))
        week2.append(q)
    intro47 = text(47).split("1.")[0].strip()
    step(
        "M15",
        [47],
        [
            {**message(47), "text": intro47},
            material(47),
            survey_action(survey_def("M15", "Двухнедельный опрос", week2)),
        ],
        days=14,
    )
    plan = extract_question(49, "plan")
    for i, o in enumerate(plan["options"]):
        o["reply"] = (
            text(49).split("Если 1-2:")[1].split("Если 3")[0].strip()
            if i < 2
            else text(49).split("Если 3 -")[1].split("Опции для")[0].strip()
        )
        o["support_actions"] = i == 2
    step(
        "M16",
        [48, 49],
        [
            message(48),
            material(48),
            {
                "id": "plan",
                "kind": "employee_resource",
                "field": "adaptation_plan_url",
                "title": "Персональный адаптационный план",
                "text": "Адаптационный план:",
                "source_ref": "XLS1-R049",
            },
            material(49),
            survey_action(survey_def("M16", "Первый месяц", [plan])),
        ],
        months=1,
    )
    step("M17", [51, 52], [message(51), message(52)], months=1, days=14)
    month_nodes = [{**message(53), "text": text(53).split("#")[0].strip()}, material(53), message(54)]
    for r, scope in [(55, ["GrPP", "Luzhniki"]), (56, ["GTP"])]:
        blocks = re.split(r"(?m)^\s*\d\)\s*", text(r))[1:]
        questions = []
        for i, block in enumerate(blocks):
            lines = block.strip().splitlines()
            labels = [
                re.sub(r"^\s*-\s*", "", line).strip() for line in lines[1:] if re.match(r"^\s*-\s*", line)
            ]
            q = {
                "id": f"MOODQ{i + 1:02}",
                "text": lines[0].replace(" (несколько вариантов ответа)", ""),
                "kind": "multiple" if i == 1 else "single",
                "source_ref": f"XLS1-R{r:03}",
                "options": [
                    {
                        "id": f"o{j + 1}",
                        "label": label.split(": возможность")[0],
                        "needs_comment": "Свой вариант" in label,
                    }
                    for j, label in enumerate(labels)
                ],
            }
            if i == 1:
                q["options"][3]["exclusive"] = True
            questions.append(q)
        replies = text(57)
        markers = [
            "Если на первый вопрос сотрудник дал 2 вариант ответа:",
            "Если на первый вопрос сотрудник дал 3 вариант ответа:",
            "Если на второй вопрос был дан 4-ый ответ:",
        ]
        extracted = [replies.split(marker)[1].split("Опции для сотрудника:")[0].strip() for marker in markers]
        for option, reply in [
            (questions[0]["options"][1], extracted[0]),
            (questions[0]["options"][2], extracted[1]),
            (questions[1]["options"][3], extracted[2]),
        ]:
            option.update(reply=reply, reply_after_survey=True, support_actions=True)
        month_nodes.append(
            survey_action(
                survey_def(
                    f"M18_{r}", "Опрос настроения и поддержки", questions, "Спасибо за обратную связь!"
                ),
                branch_codes=scope,
            )
        )
    month_nodes.insert(2, {"id": "questions_time", "kind": "wait", "at": "09:01"})
    step("M18", [53, 54, 55, 56, 57], month_nodes, months=1, days=21, at="09:00")
    step("M19", [58], [message(58), material(58)], months=2)
    step("M20", [59, 60], [message(59, categories=["office"]), material(60)], months=2, days=14)
    final = [
        extract_question(62, "FINALQ01"),
        extract_question(63, "FINALQ02"),
        {
            "id": "FINALQ03",
            "kind": "text",
            "text": text(65).removeprefix("Вопрос 3:").split("Открытый вопрос")[0].strip(),
            "source_ref": "XLS1-R065",
        },
    ]
    final[0]["options"][-1]["needs_comment"] = True
    final[1]["comment_contact_alternative"] = True
    for i, letter in enumerate(["А", "Б", "В"]):
        final[1]["options"][i].update(reply=reaction(63, letter), support_actions=i > 0)
        final[1]["options"][i]["needs_comment"] = i > 0
    farewell_text = text(68).split("Варианты ответов:")[0].strip()
    farewell = choice("farewell", farewell_text, ["Пока, Корсик! 💙", "😭😞😭😞🥺😞"], source_ref="XLS1-R068")
    playlist = {
        **message(67),
        "text": text(67).split("(наложить")[0].strip(),
        "buttons": [{"type": "link", "text": "плейлист", "url": cells["F67"]["hyperlink"]}],
    }
    step(
        "M21",
        [61, 62, 63, 65, 66, 67, 68],
        [
            message(61),
            material(61),
            {"id": "questions_time", "kind": "wait", "at": "09:01"},
            survey_action(survey_def("M21", "Итоговый опрос", final)),
            message(66),
            playlist,
            farewell,
            material(68),
        ],
        anchor="probation_end",
        at="09:00",
    )
    # A row's caption and image are one MAX message, including mandatory M19.
    for entry in steps:
        nodes = entry["actions"]
        merged = []
        index = 0
        while index < len(nodes):
            node = nodes[index]
            following = nodes[index + 1] if index + 1 < len(nodes) else None
            if (
                following
                and node["kind"] == "message"
                and following["kind"] == "material"
                and node.get("source_ref") == following.get("source_ref")
                and all(node.get(k) == following.get(k) for k in ["branch_codes", "categories", "when"])
            ):
                merged.append({**node, "kind": "material", "material_key": following["material_key"]})
                index += 2
            else:
                merged.append(node)
                index += 1
        entry["actions"] = merged
    return ScenarioDefinition.model_validate(
        {"name": "Корсик — исходный маршрут v0.4", "version": 1, "steps": steps, "surveys": surveys}
    ).model_dump(mode="json")


def install(session):
    definition = compile_profile()
    scenario = session.scalar(
        select(Scenario).where(Scenario.name == definition["name"], Scenario.version == 1)
    )
    if not scenario:
        scenario = Scenario(name=definition["name"], version=1, definition=definition, branch_codes=["*"])
        session.add(scenario)
        session.flush()
    for item in source("v04_materials.json")["items"]:
        if not session.get(Material, item["id"]):
            row = int(item["source_ref"].split("-R")[1])
            session.add(
                Material(
                    key=item["id"],
                    title=item["label"],
                    source_ref=item["source_ref"],
                    required=item["mandatory"],
                    owner=item["owner_role"],
                    branch_codes=BRANCHES.get(row, ["*"]),
                )
            )
    return scenario
