"""One authorised cohort/snapshot for preview, CSV and three-sheet XLSX."""

import csv
import hashlib
import io
import json
from collections import Counter
from datetime import date, datetime, time, timedelta
from xml.etree.ElementTree import Element, SubElement, tostring
from zipfile import ZIP_DEFLATED, ZipFile
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from pydantic import Field, model_validator
from sqlalchemy import select

from .access import can_support, in_scope, is_hr, require
from .calendar import now
from .db import utcnow
from .models import (
    AuditEvent,
    Campaign,
    Employee,
    ExportFile,
    Scenario,
    ScheduledStep,
    SurveyAnswer,
    SurveyRun,
)
from .schemas import StrictModel


class ReportInput(StrictModel):
    survey_key: str
    version: str
    from_date: date
    to_date: date
    timezone: str = "Europe/Moscow"
    branch_codes: list[str] = Field(default_factory=list)
    departments: list[str] = Field(default_factory=list)
    include_archived: bool = False
    format: str = "xlsx"
    preview_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def valid(self):
        try:
            ZoneInfo(self.timezone)
        except Exception as exc:
            raise ValueError("Неизвестный часовой пояс IANA") from exc
        if self.to_date < self.from_date:
            raise ValueError("Конец периода раньше начала")
        if self.format not in {"xlsx", "csv"}:
            raise ValueError("Выберите CSV или XLSX")
        return self


def version(definition):
    return hashlib.sha256(
        json.dumps(definition, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def preview_hash(report):
    content = {k: v for k, v in report.items() if k != "snapshot_at"}
    content["filters"] = {k: v for k, v in report["filters"].items() if k not in {"preview_hash", "format"}}
    return version(content)


def definition_for(session, step, run=None):
    if run:
        return run.definition
    if step.payload.get("survey"):
        return step.payload["survey"]
    for action in step.payload.get("actions", []):
        if action["kind"] == "survey":
            employee = session.get(Employee, step.employee_id)
            if not action.get("branch_codes") or employee.branch_code in action["branch_codes"]:
                return step.payload.get("surveys", {}).get(action["survey_id"])
    return None


def survey_key(session, step, definition):
    campaign = session.get(Campaign, step.campaign_id) if step.campaign_id else None
    scenario = session.get(Scenario, step.scenario_id)
    origin = (
        "template:" + campaign.template_id
        if campaign and campaign.template_id
        else "scenario:" + scenario.name
    )
    return origin + ":" + definition["id"]


def authorised_steps(session, actor, include_archived=False):
    require(is_hr(actor), "Подробные результаты доступны HR своей области")
    scope = True if "*" in actor.branch_codes else Employee.branch_code.in_(actor.branch_codes)
    return session.scalars(
        select(ScheduledStep)
        .join(Employee)
        .where(scope, True if include_archived else Employee.active.is_(True))
        .order_by(ScheduledStep.id)
    )


def catalogue(session, actor):
    result = {}
    for step in authorised_steps(session, actor, True):
        run = session.scalar(select(SurveyRun).where(SurveyRun.scheduled_step_id == step.id))
        definition = definition_for(session, step, run)
        if definition:
            key = survey_key(session, step, definition)
            fingerprint = version(definition)
            result[(key, fingerprint)] = {
                "survey_key": key,
                "version": fingerprint,
                "title": definition["title"],
                "questions": len(definition["questions"]),
            }
    return list(result.values())


def snapshot(session, actor, data, limit=10000):
    require(is_hr(actor))
    require(all(in_scope(actor, b) for b in data.branch_codes), "Площадка вне вашей области")
    zone = ZoneInfo(data.timezone)
    start = datetime.combine(data.from_date, time.min, zone)
    end = datetime.combine(data.to_date + timedelta(days=1), time.min, zone)
    cutoff = session.info.get("report_snapshot_at") or now(session)
    rows = []
    selected = None
    for step in authorised_steps(session, actor, data.include_archived):
        if not start <= step.due_at < end:
            continue
        employee = session.get(Employee, step.employee_id)
        if data.branch_codes and employee.branch_code not in data.branch_codes:
            continue
        if data.departments and employee.department not in data.departments:
            continue
        run = session.scalar(select(SurveyRun).where(SurveyRun.scheduled_step_id == step.id))
        definition = definition_for(session, step, run)
        if (
            not definition
            or survey_key(session, step, definition) != data.survey_key
            or version(definition) != data.version
        ):
            continue
        selected = definition
        cancelled = step.status == "cancelled" or run and run.status == "cancelled"
        status = (
            "Отменён"
            if cancelled
            else "Завершён"
            if run and run.status == "completed"
            else "В процессе"
            if run and run.status == "active"
            else "Срок истёк"
            if run and run.status == "expired" or step.status == "expired"
            else "Запланирован"
            if step.due_at > cutoff
            else "Не начал"
        )
        answers = {}
        if run:
            for answer in session.scalars(
                select(SurveyAnswer).where(SurveyAnswer.run_id == run.id, SurveyAnswer.created_at <= cutoff)
            ):
                question = next((q for q in definition["questions"] if q["id"] == answer.question_id), None)
                if not question:
                    raise HTTPException(409, f"Повреждён снимок: {run.id}")
                value = answer.value["answer"]
                options = {o["id"]: o["label"] for o in question.get("options", [])}
                if question["kind"] in {"single", "multiple"}:
                    values = value if isinstance(value, list) else [value]
                    if not set(values) <= set(options):
                        raise HTTPException(409, f"Неизвестный вариант в ответе {answer.id}")
                    display = ", ".join(o["label"] for o in question["options"] if o["id"] in values)
                else:
                    display = value
                answers[question["id"]] = {
                    "value": value,
                    "display": display,
                    "comment": answer.comment
                    if not answer.comment_created_at or answer.comment_created_at <= cutoff
                    else None,
                    "answer_id": answer.id,
                }
        rows.append(
            {
                "assignment_id": step.id,
                "run_id": run.id if run else "",
                "employee_id": employee.id,
                "name": employee.name,
                "branch_code": employee.branch_code,
                "department": employee.department,
                "campaign_id": step.campaign_id or "",
                "due_at": step.due_at.isoformat(),
                "started_at": run.started_at.isoformat() if run else "",
                "completed_at": run.completed_at.isoformat() if run and run.completed_at else "",
                "status": status,
                "awaiting_comment": bool(run and run.awaiting_comment and not run.optional_comment),
                "answers": answers,
                "cancelled": bool(cancelled),
                "due": step.due_at <= cutoff,
            }
        )
        from .models import HelpSession, Ticket

        helps = (
            list(
                session.scalars(
                    select(HelpSession).where(
                        HelpSession.employee_id == employee.id, HelpSession.created_at <= cutoff
                    )
                )
            )
            if run
            else []
        )
        helps = [h for h in helps if h.context.get("survey_run_id") == run.id]
        help_ticket_ids = {h.ticket_id for h in helps if h.ticket_id}
        answer_ids = {a["answer_id"] for a in answers.values()}
        linked = []
        if run:
            for ticket in session.scalars(
                select(Ticket).where(Ticket.employee_id == employee.id, Ticket.created_at <= cutoff)
            ):
                if (
                    ticket.id in help_ticket_ids
                    or ticket.context.get("survey_run_id") == run.id
                    or ticket.context.get("run_id") == run.id
                    or ticket.context.get("answer_id") in answer_ids
                ):
                    linked.append(ticket)
        rows[-1].update(
            help_states=[
                {"id": h.id, "status": h.status, "processor_version": h.processor_version} for h in helps
            ],
            case_states=[
                {
                    "id": t.id,
                    "status": t.status,
                    "outcome": t.outcome,
                    "confirmed_at": t.confirmed_at.isoformat() if t.confirmed_at else None,
                }
                for t in linked
            ],
        )
        if len(rows) > limit:
            raise HTTPException(413, "Слишком много назначений. Сузьте период или область")
    if not selected:
        raise HTTPException(404, "Назначений выбранной версии в этом периоде не найдено")
    base = [r for r in rows if not r["cancelled"]]
    due = [r for r in base if r["due"]]
    totals = {
        "assignments": len(rows),
        "employees": len({r["employee_id"] for r in rows}),
        "statuses": dict(Counter(r["status"] for r in rows)),
        "completion_rate": round(sum(r["status"] == "Завершён" for r in due) / len(due) * 100, 2)
        if due
        else None,
    }
    stats = []
    for question in selected["questions"]:
        answers = [r["answers"][question["id"]] for r in base if question["id"] in r["answers"]]
        values = [a["value"] for a in answers]
        item = {
            "id": question["id"],
            "text": question["text"],
            "n": len(values),
            "comments": sum(bool(a["comment"]) for a in answers),
        }
        if question["kind"] == "rating":
            if any(
                not isinstance(v, int)
                or isinstance(v, bool)
                or not question["minimum"] <= v <= question["maximum"]
                for v in values
            ):
                raise HTTPException(409, "Оценка не соответствует снимку шкалы")
            item.update(
                mean=round(sum(values) / len(values), 2) if values else None,
                distribution=dict(Counter(values)),
                below=sum(v < question["comment_below"] for v in values)
                if question.get("comment_below") is not None
                else None,
            )
        elif question["kind"] in {"single", "multiple"}:
            item["options"] = {
                o["label"]: sum(o["id"] in v if isinstance(v, list) else o["id"] == v for v in values)
                for o in question["options"]
            }
        stats.append(item)
    return {
        "snapshot_at": cutoff.isoformat(),
        "filters": data.model_dump(mode="json"),
        "definition": selected,
        "totals": totals,
        "statistics": stats,
        "rows": rows,
    }


def sheet_rows(report):
    definition = report["definition"]
    header = [
        "Назначение ID",
        "Прохождение ID",
        "Сотрудник ID",
        "ФИО",
        "Площадка",
        "Подразделение",
        "Рассылка ID",
        "Плановый запуск",
        "Начало",
        "Завершение",
        "Статус",
        "Ответов",
        "Всего вопросов",
        "Ожидается комментарий",
        "Опрос",
        "Версия",
        "Срез",
        "Часовой пояс",
        "Помощь ИИ — состояния",
        "Связанные обращения — состояния",
        "Решений подтверждено сотрудником",
    ]
    for q in definition["questions"]:
        header += [q["id"] + " — ответ", q["id"] + " — комментарий"]
    rows = [header]
    for row in report["rows"]:
        values = [
            row[k]
            for k in [
                "assignment_id",
                "run_id",
                "employee_id",
                "name",
                "branch_code",
                "department",
                "campaign_id",
                "due_at",
                "started_at",
                "completed_at",
                "status",
            ]
        ]
        values += [
            len(row["answers"]),
            len(definition["questions"]),
            "Да" if row["awaiting_comment"] else "Нет",
        ]
        values += [
            definition["title"],
            report["filters"]["version"],
            report["snapshot_at"],
            report["filters"]["timezone"],
        ]
        help_labels = {
            "resolved": "Полезность подтверждена",
            "awaiting_feedback": "Ожидается оценка полезности",
            "awaiting_clarification": "Ожидается уточнение",
            "escalated": "Передано HR",
            "error": "Ошибка",
            "recipient_choice": "Выбор HR",
        }
        case_labels = {
            "open": "Новое",
            "in_progress": "В работе",
            "awaiting_confirmation": "Ожидает подтверждения сотрудника",
            "closed": "Закрыто",
        }
        values += [
            "; ".join(h["id"] + ": " + help_labels.get(h["status"], h["status"]) for h in row["help_states"]),
            "; ".join(
                t["id"]
                + ": "
                + case_labels.get(t["status"], t["status"])
                + (
                    " (подтверждено сотрудником)"
                    if t["outcome"] == "employee_confirmed"
                    else " (без подтверждения)"
                    if t["status"] == "closed"
                    else ""
                )
                for t in row["case_states"]
            ),
            sum(t["outcome"] == "employee_confirmed" for t in row["case_states"]),
        ]
        for q in definition["questions"]:
            answer = row["answers"].get(q["id"], {})
            values += [answer.get("display"), answer.get("comment")]
        rows.append(values)
    summary = [
        ["Показатель", "Значение"],
        ["Назначений", report["totals"]["assignments"]],
        ["Уникальных сотрудников", report["totals"]["employees"]],
        ["Доля завершения, %", report["totals"]["completion_rate"]],
    ]
    summary += [["Статус: " + key, value] for key, value in report["totals"]["statuses"].items()]
    for q in report["statistics"]:
        summary += [[q["id"] + ": N", q["n"]], [q["id"] + ": комментариев", q["comments"]]]
        if "mean" in q:
            summary += [[q["id"] + ": среднее", q["mean"]], [q["id"] + ": ниже порога", q["below"]]] + [
                [q["id"] + f": оценка {key}", value] for key, value in q["distribution"].items()
            ]
        for label, count in q.get("options", {}).items():
            summary += [
                [q["id"] + ": " + label, count],
                [q["id"] + ": доля " + label, round(count / q["n"] * 100, 2) if q["n"] else None],
            ]
    description = [
        ["Параметр", "Значение"],
        ["Опрос", definition["title"]],
        ["Срез", report["snapshot_at"]],
        ["Фильтры", json.dumps(report["filters"], ensure_ascii=False)],
        ["Период", "По плановой дате назначения; конец периода исключён"],
        ["Организация", "Текущие площадка/подразделение и права на момент экспорта"],
        [
            "Среднее",
            "По принятым оценкам конкретного вопроса, включая частичные ответы; отменённые исключены; отсутствие не равно нулю",
        ],
        [
            "Доля завершения",
            "Завершённые / уже наступившие неотменённые назначения; нулевой знаменатель — нет данных",
        ],
        ["Множественный выбор", "Доля от ответивших; сумма долей может превышать 100%"],
        ["ML", "Файл формируется без вызова ML; опрос не является анонимным"],
    ]
    for q in definition["questions"]:
        description += [[q["id"], q["text"]], [q["id"] + ": правила", json.dumps(q, ensure_ascii=False)]]
    return {"Результаты": rows, "Сводка": summary, "Описание": description}


def csv_bytes(rows):
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, delimiter=";")
    for row in rows:
        safe = []
        for value in row:
            if isinstance(value, str) and (
                value.lstrip().startswith(("=", "+", "-", "@"))
                or value.startswith(("\t", "\r"))
                or value.isdigit()
                and value.startswith("0")
            ):
                value = "'" + value
            safe.append("" if value is None else value)
        writer.writerow(safe)
    return buffer.getvalue().encode("utf-8-sig")


def xlsx_bytes(sheets):
    """Small standard-library OOXML writer: typed numbers, safe inline strings, no formulas."""
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"

    def column(index):
        value = ""
        while index:
            index, rem = divmod(index - 1, 26)
            value = chr(65 + rem) + value
        return value

    def xml(element):
        return b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' + tostring(
            element, encoding="utf-8"
        )

    out = io.BytesIO()
    with ZipFile(out, "w", ZIP_DEFLATED) as book:
        types = Element("Types", xmlns="http://schemas.openxmlformats.org/package/2006/content-types")
        for ext, mime in [
            ("rels", "application/vnd.openxmlformats-package.relationships+xml"),
            ("xml", "application/xml"),
        ]:
            SubElement(types, "Default", Extension=ext, ContentType=mime)
        for part, mime in [
            (
                "/xl/workbook.xml",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
            ),
            ("/xl/styles.xml", "application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"),
        ] + [
            (
                f"/xl/worksheets/sheet{i}.xml",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml",
            )
            for i in range(1, len(sheets) + 1)
        ]:
            SubElement(types, "Override", PartName=part, ContentType=mime)
        book.writestr("[Content_Types].xml", xml(types))
        book.writestr(
            "_rels/.rels",
            b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        )
        workbook = Element("workbook", xmlns=ns)
        workbook.set("xmlns:r", "http://schemas.openxmlformats.org/officeDocument/2006/relationships")
        ws = SubElement(workbook, "sheets")
        rels = Element("Relationships", xmlns="http://schemas.openxmlformats.org/package/2006/relationships")
        for i, (name, rows) in enumerate(sheets.items(), 1):
            SubElement(ws, "sheet", name=name, sheetId=str(i), attrib={"r:id": f"rId{i}"})
            SubElement(
                rels,
                "Relationship",
                Id=f"rId{i}",
                Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet",
                Target=f"worksheets/sheet{i}.xml",
            )
            sheet = Element("worksheet", xmlns=ns)
            views = SubElement(sheet, "sheetViews")
            view = SubElement(views, "sheetView", workbookViewId="0", showGridLines="0")
            SubElement(
                view,
                "pane",
                xSplit="3" if i == 1 else "0",
                ySplit="1",
                topLeftCell="D2" if i == 1 else "A2",
                state="frozen",
            )
            cols = SubElement(sheet, "cols")
            for j in range(1, max(map(len, rows)) + 1):
                SubElement(
                    cols,
                    "col",
                    min=str(j),
                    max=str(j),
                    width="42" if j > 3 or i != 1 else "27",
                    customWidth="1",
                )
            data = SubElement(sheet, "sheetData")
            for r, values in enumerate(rows, 1):
                row = SubElement(data, "row", r=str(r), ht="32" if r == 1 else "45", customHeight="1")
                for c, value in enumerate(values, 1):
                    if value is None:
                        continue
                    cell = SubElement(row, "c", r=column(c) + str(r), s="1" if r == 1 else "0")
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        SubElement(cell, "v").text = str(value)
                    else:
                        import re

                        cell.set("t", "inlineStr")
                        content = SubElement(SubElement(cell, "is"), "t")
                        content.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
                        content.text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(value))
            SubElement(sheet, "autoFilter", ref=f"A1:{column(max(map(len, rows)))}{len(rows)}")
            book.writestr(f"xl/worksheets/sheet{i}.xml", xml(sheet))
        SubElement(
            rels,
            "Relationship",
            Id="styles",
            Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles",
            Target="styles.xml",
        )
        book.writestr("xl/workbook.xml", xml(workbook))
        book.writestr("xl/_rels/workbook.xml.rels", xml(rels))
        book.writestr(
            "xl/styles.xml",
            f'<styleSheet xmlns="{ns}"><fonts count="2"><font><sz val="10"/><name val="Arial"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="10"/><name val="Arial"/></font></fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF163447"/></patternFill></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf/></cellStyleXfs><cellXfs count="2"><xf fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf><xf fontId="1" fillId="2" borderId="0" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>',
        )
    return out.getvalue()


def create_file(session, actor, settings, data):
    report = snapshot(session, actor, data)
    if data.preview_hash and data.preview_hash != preview_hash(report):
        raise HTTPException(409, "Данные изменились после предпросмотра. Обновите предпросмотр")
    sheets = sheet_rows(report)
    content = xlsx_bytes(sheets) if data.format == "xlsx" else csv_bytes(sheets["Результаты"])
    item = ExportFile(
        actor_id=actor.id,
        filters={**report["filters"], "snapshot_at": report["snapshot_at"], "profile": "survey_results_v1"},
        employee_ids=sorted({r["employee_id"] for r in report["rows"]}),
        filename="results." + data.format,
        row_count=len(report["rows"]),
        expires_at=utcnow() + timedelta(minutes=15),
    )
    session.add(item)
    session.flush()
    folder = settings.data_dir / "exports"
    folder.mkdir(exist_ok=True)
    (folder / item.id).write_bytes(content)
    session.add(AuditEvent(actor_id=actor.id, action="report.export", resource_id=item.id))
    return item


def can_download(session, actor, item):
    return bool(
        actor
        and actor.active
        and is_hr(actor)
        and actor.id == item.actor_id
        and item.expires_at > utcnow()
        and all((e := session.get(Employee, eid)) and can_support(actor, e) for eid in item.employee_ids)
    )
