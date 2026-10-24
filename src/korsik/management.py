"""Structured management tasks with explicit grants and durable, scoped history."""

import hashlib
import json

from fastapi import HTTPException
from sqlalchemy import select

from . import access, documents
from .admin_service import audit
from .calendar import now
from .extension_models import (
    AssignmentGrant,
    ImprovementInsight,
    ImprovementReport,
    ManagementConversation,
    ManagementEntry,
    ManagementReadState,
    ManagementTask,
)
from .models import Branch, Document, Identity, uid
from .queue import send

OPEN = {"assigned", "in_progress", "blocked"}


def staff(actor):
    access.require(actor.active and access.is_hr(actor))


def can_read(session, actor, task):
    if not actor.active or not access.in_scope(actor, task.branch_code):
        return False
    conversation = session.get(ManagementConversation, task.conversation_id)
    return bool(
        conversation
        and (
            actor.id == conversation.issuer_id
            and "hr_head" in actor.roles
            or actor.id == conversation.assignee_id
            and access.is_hr(actor)
        )
    )


def get_task(session, actor, task_id):
    task = session.scalar(select(ManagementTask).where(ManagementTask.id == task_id).with_for_update())
    if not task or not can_read(session, actor, task):
        raise HTTPException(404, "Поручение не найдено")
    return task


def assignees(session, actor):
    access.require(actor.active and "hr_head" in actor.roles)
    result = []
    for grant in session.scalars(
        select(AssignmentGrant).where(
            AssignmentGrant.issuer_id == actor.id,
            AssignmentGrant.active.is_(True),
        )
    ):
        person = session.get(Identity, grant.assignee_id)
        branch = session.get(Branch, grant.branch_code)
        if (
            person
            and person.active
            and access.is_hr(person)
            and branch
            and branch.active
            and access.in_scope(actor, branch.code)
            and access.in_scope(person, branch.code)
        ):
            result.append({"id": person.id, "name": person.name, "branch_code": branch.code})
    return result


def digest(data):
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()


def replay(session, actor, key, payload):
    request_key = actor.id + ":" + key
    entry = session.scalar(select(ManagementEntry).where(ManagementEntry.request_key == request_key))
    if entry and entry.body.get("request_hash") != digest(payload):
        raise HTTPException(409, "Ключ операции уже использован с другими данными")
    return entry, request_key


def append(session, actor, task, kind, body, key, payload):
    conversation = session.scalar(
        select(ManagementConversation)
        .where(
            ManagementConversation.id == task.conversation_id,
        )
        .with_for_update()
    )
    conversation.last_seq += 1
    entry = ManagementEntry(
        conversation_id=conversation.id,
        task_id=task.id,
        seq=conversation.last_seq,
        actor_id=actor.id,
        kind=kind,
        body={
            "task_title": task.title,
            "task_number": task.display_number,
            **body,
            "request_hash": digest(payload),
        },
        request_key=key,
        created_at=now(session),
    )
    session.add(entry)
    session.flush()
    audit(session, actor, "management." + kind, task.id)
    if kind in {"assigned", "block", "clarify", "answer_blocker", "reopen", "cancel"}:
        recipient = conversation.assignee_id if actor.id == conversation.issuer_id else conversation.issuer_id
        item = send(
            session,
            recipient,
            f"Поручение {task.display_number}: {task.title}\n"
            + {
                "assigned": "Новое поручение",
                "block": "Возникло препятствие",
                "clarify": "Уточнение",
                "answer_blocker": "Ответ на препятствие",
                "reopen": "Возвращено в работу",
                "cancel": "Отменено",
            }.get(kind, kind)
            + "\nОткройте раздел «Поручения».",
            "management:" + entry.id,
            now(session),
        )
        item.body = {**item.body, "management_task_id": task.id, "management_entry_id": entry.id}
    return entry


def create_task(session, actor, data, settings=None):
    access.require(actor.active and "hr_head" in actor.roles)
    session.scalar(select(Identity).where(Identity.id == actor.id).with_for_update())
    if data.mode == "demo" and (not settings or not settings.enable_demo):
        raise HTTPException(403, "Демонстрационный режим отключён")
    payload = data.model_dump(mode="json")
    previous, key = replay(session, actor, data.request_key, payload)
    if previous:
        return get_task(session, actor, previous.task_id)
    if not any(
        p["id"] == data.assignee_id and p["branch_code"] == data.branch_code
        for p in assignees(session, actor)
    ):
        raise HTTPException(403, "Нет разрешения выдавать поручения этому HR в этой области")
    access.require(actor.id != data.assignee_id, "Самопоручения не поддерживаются")
    snapshot = {}
    if data.source_insight_id:
        from .improvements import policy, read_report

        config = policy(session)
        if not config.enabled or config.mode != data.mode:
            raise HTTPException(409, "Аналитическое основание отключено или изменился режим")
        insight = session.scalar(
            select(ImprovementInsight)
            .where(
                ImprovementInsight.id == data.source_insight_id,
            )
            .with_for_update()
        )
        report = session.get(ImprovementReport, data.source_report_id) if data.source_report_id else None
        if report:
            read_report(session, actor, report.id)
            if report.snapshot["policy"] != config.model_dump(mode="json"):
                raise HTTPException(409, "Политика отчёта изменилась; сформируйте новый снимок")
        if (
            not insight
            or insight.branch_code != data.branch_code
            or not report
            or report.actor_id != actor.id
            or insight.mode != report.mode
            or data.mode != report.mode
        ):
            raise HTTPException(404, "Основание недоступно")
        snapshot = next((x for x in report.snapshot["items"] if x["id"] == insight.id), None)
        if not snapshot:
            raise HTTPException(404, "В этом снимке нет разрешённого кандидата")
        existing = session.scalar(
            select(ManagementTask).where(
                ManagementTask.source_insight_id == insight.id,
                ManagementTask.status.in_(OPEN),
            )
        )
        if existing:
            if can_read(session, actor, existing):
                raise HTTPException(409, {"message": "Уже есть активное поручение", "task_id": existing.id})
            raise HTTPException(409, "По этому основанию уже ведётся работа")
    if data.replaces_task_id:
        replaced = get_task(session, actor, data.replaces_task_id)
        if replaced.status != "cancelled":
            raise HTTPException(409, "Перед перевыдачей отмените прежнее поручение")
    conversation = session.scalar(
        select(ManagementConversation)
        .where(
            ManagementConversation.issuer_id == actor.id,
            ManagementConversation.assignee_id == data.assignee_id,
        )
        .with_for_update()
    )
    if not conversation:
        conversation = ManagementConversation(issuer_id=actor.id, assignee_id=data.assignee_id)
        session.add(conversation)
        session.flush()
    task_id = uid()
    task = ManagementTask(
        id=task_id,
        display_number="П-" + task_id[:8].upper(),
        conversation_id=conversation.id,
        branch_code=data.branch_code,
        title=data.title,
        description=data.description,
        expected_result=data.expected_result,
        due_at=data.due_at,
        source_insight_id=data.source_insight_id,
        source_report_id=data.source_report_id,
        source_snapshot=snapshot,
        replaces_task_id=data.replaces_task_id,
        created_at=now(session),
    )
    task.mode = data.mode
    session.add(task)
    session.flush()
    append(session, actor, task, "assigned", {"description": task.description}, key, payload)
    return task


def command(session, actor, task, data, settings):
    payload = {"task_id": task.id, **data.model_dump(mode="json")}
    previous, key = replay(session, actor, data.request_key, payload)
    if previous:
        return task
    if data.revision != task.revision:
        raise HTTPException(409, "Поручение изменилось. Обновите карточку")
    conversation = session.get(ManagementConversation, task.conversation_id)
    is_assignee = actor.id == conversation.assignee_id
    assignee_commands = {"accept", "block", "resume", "complete"}
    access.require(
        (is_assignee and data.action in assignee_commands)
        or (actor.id == conversation.issuer_id and data.action not in assignee_commands)
    )
    if task.status == "cancelled" or (task.status == "completed" and data.action != "reopen"):
        raise HTTPException(409, "Для этого состояния действие недоступно")
    if data.action == "accept":
        if task.status != "assigned":
            raise HTTPException(409, "Принять можно только новое поручение")
        task.status = "in_progress"
    elif data.action == "block":
        if task.status not in {"assigned", "in_progress"} or not data.text:
            raise HTTPException(422, "Опишите препятствие для открытого поручения")
        task.status = "blocked"
    elif data.action == "resume":
        if task.status != "blocked":
            raise HTTPException(409, "Поручение не заблокировано")
        task.status = "in_progress"
    elif data.action == "answer_blocker":
        if task.status != "blocked" or not data.text:
            raise HTTPException(422, "Нужен ответ на действующее препятствие")
    elif data.action == "clarify":
        if not data.text:
            raise HTTPException(422, "Введите уточнение")
    elif data.action in {"reopen", "cancel"}:
        if not data.text or (data.action == "reopen" and task.status != "completed"):
            raise HTTPException(422, "Укажите причину допустимого перехода")
        task.status = "in_progress" if data.action == "reopen" else "cancelled"
        if data.action == "reopen" and task.source_insight_id:
            session.scalar(
                select(ImprovementInsight)
                .where(ImprovementInsight.id == task.source_insight_id)
                .with_for_update()
            )
            if session.scalar(
                select(ManagementTask.id).where(
                    ManagementTask.source_insight_id == task.source_insight_id,
                    ManagementTask.id != task.id,
                    ManagementTask.status.in_(OPEN),
                )
            ):
                raise HTTPException(409, "Уже есть другое активное поручение по этой теме")
    elif data.action == "update":
        if any(
            name in data.model_fields_set and getattr(data, name) is None
            for name in ("title", "description", "expected_result")
        ):
            raise HTTPException(422, "Название, описание и ожидаемый результат не могут быть null")
        if not set(data.model_fields_set) & {"title", "description", "expected_result", "due_at"}:
            raise HTTPException(422, "Укажите изменяемые поля")
        for name in ("title", "description", "expected_result", "due_at"):
            if name in data.model_fields_set:
                setattr(task, name, getattr(data, name))
    elif data.action == "complete":
        if not data.result_kind or not data.text:
            raise HTTPException(422, "Выберите вид результата и опишите выполненное действие или причину")
        if data.result_kind == "knowledge_updated":
            doc = session.get(Document, data.document_id) if data.document_id else None
            status_allowed = (
                {"ready", "ready_stub"} if settings.enable_demo and task.mode == "demo" else {"ready"}
            )
            if (
                not doc
                or doc.purpose != "knowledge"
                or not doc.active
                or not doc.published
                or doc.status not in status_allowed
                or doc.version != data.document_version
                or not documents.can_edit(actor, doc)
                or not ("*" in doc.branch_codes or task.branch_code in doc.branch_codes)
            ):
                raise HTTPException(
                    422, "Нужна готовая действующая версия корпоративного документа в вашей области"
                )
        elif data.document_id or data.document_version:
            raise HTTPException(422, "Ссылку на документ выбирают только для обновления базы знаний")
        task.result = {
            "kind": data.result_kind,
            "text": data.text,
            "document_id": data.document_id,
            "document_version": data.document_version,
            "demo": bool(data.result_kind == "knowledge_updated" and doc.status == "ready_stub"),
        }
        task.status, task.completed_at = "completed", now(session)
    task.revision += 1
    append(
        session,
        actor,
        task,
        data.action,
        {
            "text": data.text,
            "status": task.status,
            "result": task.result,
            "revision": task.revision,
            "title": task.title,
        },
        key,
        payload,
    )
    session.flush()
    return task


def task_dto(session, actor, task):
    conversation = session.get(ManagementConversation, task.conversation_id)
    assignee = session.get(Identity, conversation.assignee_id)
    return {
        "id": task.id,
        "display_number": task.display_number,
        "conversation_id": task.conversation_id,
        "mode": task.mode,
        "issuer_id": conversation.issuer_id,
        "assignee_id": conversation.assignee_id,
        "assignee_name": assignee.name,
        "assignee_unavailable": not assignee.active or not access.is_hr(assignee),
        "branch_code": task.branch_code,
        "title": task.title,
        "description": task.description,
        "expected_result": task.expected_result,
        "status": task.status,
        "revision": task.revision,
        "due_at": task.due_at,
        "overdue": bool(task.status in OPEN and task.due_at and task.due_at < now(session)),
        "result": task.result,
        "source_snapshot": task.source_snapshot,
        "source_insight_id": task.source_insight_id,
        "replaces_task_id": task.replaces_task_id,
        "can_execute": actor.id == conversation.assignee_id,
        "can_direct": actor.id == conversation.issuer_id,
    }


def visible_tasks(session, actor):
    staff(actor)
    return [
        t
        for t in session.scalars(
            select(ManagementTask).order_by(ManagementTask.created_at, ManagementTask.id)
        )
        if can_read(session, actor, t)
    ]


def conversation_entries(session, actor, conversation_id, *, after=0, limit=50):
    tasks = [t for t in visible_tasks(session, actor) if t.conversation_id == conversation_id]
    if not tasks:
        raise HTTPException(404, "Диалог не найден")
    task_ids = [t.id for t in tasks]
    entries = list(
        session.scalars(
            select(ManagementEntry)
            .where(
                ManagementEntry.conversation_id == conversation_id,
                ManagementEntry.task_id.in_(task_ids),
                ManagementEntry.seq > after,
            )
            .order_by(ManagementEntry.seq)
            .limit(limit + 1)
        )
    )
    page = entries[:limit]
    return {
        "items": [
            {
                "id": x.id,
                "task_id": x.task_id,
                "seq": x.seq,
                "actor_id": x.actor_id,
                "kind": x.kind,
                "body": {k: v for k, v in x.body.items() if k != "request_hash"},
                "created_at": x.created_at,
            }
            for x in page
        ],
        "next_after": page[-1].seq if len(entries) > limit else None,
        "last_seen_seq": page[-1].seq if page else after,
    }


def summaries(session, actor):
    groups = {}
    tasks = visible_tasks(session, actor)
    for task in tasks:
        groups.setdefault(task.conversation_id, []).append(task)
    result = []
    for conv_id, group in groups.items():
        state = session.get(ManagementReadState, (conv_id, actor.id))
        entries = list(
            session.scalars(
                select(ManagementEntry)
                .where(ManagementEntry.task_id.in_([t.id for t in group]))
                .order_by(ManagementEntry.seq)
            )
        )
        incoming = [x for x in entries if x.actor_id != actor.id and x.seq > (state.last_seq if state else 0)]
        conv = session.get(ManagementConversation, conv_id)
        partner = session.get(Identity, conv.assignee_id if actor.id == conv.issuer_id else conv.issuer_id)
        result.append(
            {
                "id": conv_id,
                "partner_name": partner.name,
                "unread_count": len(incoming),
                "open_tasks": sum(t.status in OPEN for t in group),
                "preview": next(t.title for t in group if t.id == entries[-1].task_id) if entries else "",
                "last_seq": entries[-1].seq if entries else 0,
            }
        )
    return result


def mark_read(session, actor, conversation_id, through_seq):
    entries = conversation_entries(session, actor, conversation_id, after=max(0, through_seq - 1), limit=1)
    if through_seq and not any(e["seq"] == through_seq for e in entries["items"]):
        raise HTTPException(422, "Можно отметить только доступное событие")
    state = session.get(ManagementReadState, (conversation_id, actor.id))
    if not state:
        state = ManagementReadState(conversation_id=conversation_id, identity_id=actor.id, last_seq=0)
        session.add(state)
    state.last_seq = max(state.last_seq, through_seq)
    return {"last_read_seq": state.last_seq}


def delivery_allowed(session, item):
    task_id = item.body.get("management_task_id")
    if not task_id:
        return True
    task = session.get(ManagementTask, task_id)
    actor = session.get(Identity, item.recipient_id)
    entry = session.get(ManagementEntry, item.body.get("management_entry_id"))
    return bool(
        task
        and actor
        and entry
        and can_read(session, actor, task)
        and (task.status != "cancelled" or entry.kind == "cancel")
        and (entry.kind != "block" or task.status == "blocked")
        and (entry.kind not in {"assigned", "reopen"} or task.status in OPEN)
    )
