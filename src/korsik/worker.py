import logging
import time
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select

from . import bot, documents, onboarding, scenarios, surveys
from .calendar import now
from .db import utcnow
from .max_client import DeliveryError, normalize_update
from .models import (
    Branch,
    Document,
    Employee,
    FeedbackAnalysis,
    Identity,
    Job,
    Outbox,
    ScheduledStep,
    State,
    SurveyAnswer,
    SurveyRun,
    uid,
)
from .queue import enqueue, send

log = logging.getLogger("korsik.worker")


def ingest(session, event):
    return enqueue(session, "incoming", event, "incoming:" + event["event_id"], now(session))


class Worker:
    def __init__(self, runtime):
        self.runtime = runtime
        self.owner = uid()

    def schedule(self):
        with self.runtime.db.transaction(write_lock=True) as session:
            at = now(session)
            from .campaigns import expire_assignments

            expire_assignments(session, at)
            for employee in session.scalars(
                select(Employee)
                .where(
                    Employee.onboarding_status == "active",
                    Employee.probation_end <= (at + timedelta(days=1)).date(),
                )
                .with_for_update()
            ):
                onboarding.sync(session, employee)
            surveys.expire_runs(session)
            due = session.scalars(
                select(ScheduledStep)
                .join(Employee)
                .join(Branch, Employee.branch_code == Branch.code)
                .join(Identity, Employee.identity_id == Identity.id)
                .where(
                    ScheduledStep.status == "pending",
                    ScheduledStep.due_at <= at,
                    Employee.active.is_(True),
                    Employee.status == "active",
                    Identity.active.is_(True),
                    Identity.max_user_id.is_not(None),
                    Branch.active.is_(True),
                )
                .order_by(ScheduledStep.due_at, ScheduledStep.order_index, ScheduledStep.id)
                .limit(500)
                .with_for_update()
            )
            for step in due:
                enqueue(session, "step", {"step_id": step.id}, f"step:{step.id}", at)
                step.status = "enqueued"
            queued_employees = session.scalars(
                select(Employee)
                .where(
                    Employee.active.is_(True),
                    Employee.status == "active",
                    Employee.id.in_(
                        select(ScheduledStep.employee_id).where(ScheduledStep.status == "queued")
                    ),
                )
                .limit(500)
                .with_for_update()
            )
            for employee in queued_employees:
                scenarios.drain_dialogue_queue(session, employee)
            from .routes import retry_blocked

            retry_blocked(session)
            from .meetings import route_pending

            route_pending(session)
            self.schedule_digests(session, at)

    def schedule_digests(self, session, at):
        config = session.get(State, "digest_policy")
        policy = (
            config.value
            if config
            else {"enabled": True, "timezone": "Europe/Moscow", "weekday": 0, "hour": 9}
        )
        if not policy.get("enabled", True):
            return
        local = at.astimezone(ZoneInfo(policy["timezone"]))
        day = local.date() - timedelta(days=(local.weekday() - policy["weekday"]) % 7)
        boundary = datetime.combine(day, datetime.min.time(), local.tzinfo).replace(hour=policy["hour"])
        if local < boundary:
            return
        boundary = boundary.astimezone(UTC)
        for identity in session.scalars(select(Identity).where(Identity.active.is_(True))):
            if "hr_head" in identity.roles:
                enqueue(
                    session,
                    "digest",
                    {"identity_id": identity.id, "end": boundary.isoformat()},
                    f"digest:{identity.id}:{boundary.date()}",
                    at,
                )

    def claim_job(self):
        with self.runtime.db.transaction(write_lock=True) as session:
            at = now(session)
            job = session.scalar(
                select(Job)
                .where(
                    or_(
                        (Job.status.in_(["pending", "retry"])) & (Job.due_at <= at),
                        (Job.status == "running") & (Job.lease_until < utcnow()),
                    )
                )
                .order_by(Job.due_at, Job.created_at, Job.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if not job:
                return None
            job.status = "running"
            job.attempts += 1
            job.lease_owner = self.owner
            job.lease_until = utcnow() + timedelta(seconds=self.runtime.settings.job_lease_seconds)
            return job.id

    def process_one(self):
        job_id = self.claim_job()
        if not job_id:
            return False
        try:
            with self.runtime.db.transaction(write_lock=True) as session:
                job = session.scalar(select(Job).where(Job.id == job_id).with_for_update())
                if job.status != "running" or job.lease_owner != self.owner:
                    return True
                self.handle(session, job)
                job.status = "done"
                job.last_error = None
                # Invite secrets and incoming private text need not remain in the job queue after processing.
                if job.kind == "incoming":
                    job.payload = {"event_id": job.payload["event_id"]}
            log.info("job_done kind=%s id=%s", job.kind, job_id)
        except Exception as exc:
            # Log IDs and error classes, never request payloads, tokens or employee text.
            log.warning("job_failed id=%s error=%s", job_id, type(exc).__name__)
            with self.runtime.db.transaction(write_lock=True) as session:
                job = session.get(Job, job_id)
                if job.lease_owner == self.owner:
                    job.last_error = type(exc).__name__
                    job.status = (
                        "failed" if job.attempts >= self.runtime.settings.max_job_attempts else "retry"
                    )
                    job.due_at = now(session) + timedelta(seconds=min(300, 2**job.attempts))
                    if job.status == "failed" and job.kind in {"index_document", "index_status"}:
                        doc = session.get(Document, job.payload.get("document_id"))
                        if doc:
                            doc.status = "failed"
                            doc.last_error = (
                                "Ошибка обработки: " + type(exc).__name__ + ". Повторите обработку."
                            )
        return True

    def handle(self, session, job):
        data = job.payload
        if job.kind == "incoming":
            bot.receive(self.runtime, session, data)
            if data.get("callback_id"):
                enqueue(
                    session,
                    "max_ack",
                    {"callback_id": data["callback_id"]},
                    "ack:" + data["callback_id"],
                    now(session),
                )
        elif job.kind == "max_ack":
            self.runtime.transport.acknowledge(data["callback_id"])
        elif job.kind == "step":
            step = session.scalar(
                select(ScheduledStep).where(ScheduledStep.id == data["step_id"]).with_for_update()
            )
            if step:
                employee = session.scalar(
                    select(Employee).where(Employee.id == step.employee_id).with_for_update()
                )
                scenarios.dispatch_step(session, employee, step)
        elif job.kind == "reminder":
            run = session.get(SurveyRun, data["run_id"])
            if run and surveys.reminder_allowed(session, run.id):
                employee = session.get(Employee, run.employee_id)
                step = session.get(ScheduledStep, run.scheduled_step_id)
                if (
                    employee.active
                    and employee.status == "active"
                    and (step.campaign_id or employee.onboarding_status != "stopped")
                    and (not run.expires_at or now(session) < run.expires_at)
                ):
                    item = send(
                        session,
                        employee.identity_id,
                        "Напоминаю о незавершённом опросе. Продолжить: /resume.",
                        job.dedupe_key,
                        now(session),
                        buttons=[{"text": "Продолжить опрос", "payload": "resume:" + run.id}],
                    )
                    item.body = {**item.body, "reminder_run_id": run.id}
            else:
                job.payload = {
                    **job.payload,
                    "suppressed_reason": "Опрос недоступен или первое сообщение не доставлено",
                }
        elif job.kind == "analyze":
            self.analyze(session, job)
        elif job.kind == "personal_plan":
            from . import access, optional

            employee = session.get(Employee, data["employee_id"])
            author = session.get(Identity, data["author_id"])
            current = optional.plan(session, employee) if employee else None
            if (
                employee
                and author
                and author.active
                and access.can_support(author, employee)
                and current
                and current.id == data["document_id"]
            ):
                item = optional.send_plan(session, employee, job.dedupe_key)
                item.body = {**item.body, "plan_author_id": author.id, "plan_employee_id": employee.id}
            else:
                job.payload = {**job.payload, "suppressed_reason": "plan_or_access_changed"}
        elif job.kind in {"index_document", "index_status"}:
            self.index_document(session, job)
        elif job.kind == "digest":
            actor = session.get(Identity, data["identity_id"])
            if actor and actor.active and "hr_head" in actor.roles:
                end = datetime.fromisoformat(data["end"])
                from .digests import create_snapshot

                snapshot = create_snapshot(session, actor, end, job.dedupe_key, self.runtime.settings)
                item = send(session, actor.id, snapshot.text, job.dedupe_key, now(session))
                item.body = {**item.body, "digest_snapshot_id": snapshot.id}
        else:
            raise ValueError("Unknown job kind")

    def analyze(self, session, job):
        data = job.payload
        answer = session.get(SurveyAnswer, data["answer_id"])
        employee = session.get(Employee, data["employee_id"])
        if session.scalar(select(FeedbackAnalysis.id).where(FeedbackAnalysis.answer_id == answer.id)):
            return
        result = self.runtime.ml.analyze(
            {
                "request_id": job.dedupe_key,
                "employee_id": employee.id,
                "question": data["question"],
                "text": answer.comment or str(answer.value["answer"]),
                "answer": answer.value,
                "access": {"branch_code": employee.branch_code, "category": employee.category},
            }
        )
        analysis = FeedbackAnalysis(
            answer_id=answer.id,
            employee_id=employee.id,
            processor_version=result.processor_version,
            result=result.model_dump(),
            created_at=now(session),
        )
        session.add(analysis)
        session.flush()
        from .feedback import apply_policy

        apply_policy(session, employee, analysis, result)

    def index_document(self, session, job):
        doc = session.get(Document, job.payload["document_id"])
        if not doc or not doc.active or doc.purpose != "knowledge":
            return
        if job.kind == "index_document":
            result = self.runtime.ml.index(
                doc, self.runtime.settings.data_dir / "documents" / doc.storage_name
            )
        else:
            result = self.runtime.ml.index_status(doc.external_id)
        doc.external_id = result.external_id
        doc.processor_version = result.processor_version
        doc.status = (
            "ready_stub"
            if result.status == "ready" and self.runtime.settings.ml_mode == "stub"
            else result.status
        )
        doc.last_error = (
            "Не удалось обработать документ. Повторите обработку или загрузите другой файл."
            if result.status == "failed"
            else None
        )
        if result.status == "ready" and doc.auto_publish:
            newer = session.scalar(
                select(Document.id)
                .where(
                    Document.family_id == (doc.family_id or doc.id),
                    Document.version > doc.version,
                    Document.published.is_(True),
                    Document.active.is_(True),
                )
                .limit(1)
            )
            if not newer:
                documents.publish(session, doc)
        if result.status == "processing":
            poll = job.payload.get("poll", 0) + 1
            if poll > 240:
                doc.status = "failed"
                doc.last_error = "Превышено время ожидания обработки. Запустите обработку повторно."
                return
            enqueue(
                session,
                "index_status",
                {"document_id": doc.id, "poll": poll},
                f"index-poll:{doc.id}:{poll}",
                now(session) + timedelta(seconds=30),
            )

    def extension_delivery_allowed(self, session, item):
        from . import access, management, optional

        if item.body.get("planning_id") and not self.runtime.settings.enable_optional:
            return False
        if item.body.get("management_task_id") and not self.runtime.settings.enable_management:
            return False
        if not optional.delivery_allowed(session, item) or not management.delivery_allowed(session, item):
            return False
        if item.body.get("plan_author_id"):
            author = session.get(Identity, item.body["plan_author_id"])
            owner = session.get(Employee, item.body["plan_employee_id"])
            if not author or not author.active or not owner or not access.can_support(author, owner):
                return False
        if item.body.get("reminder_run_id") and not surveys.reminder_allowed(
            session, item.body["reminder_run_id"]
        ):
            return False
        for doc_id in item.body.get("required_document_ids", []):
            doc = session.get(Document, doc_id)
            actor = session.get(Identity, item.recipient_id) if item.recipient_id else None
            if not doc or not actor or not actor.active or not documents.can_read(session, actor, doc):
                return False
            if doc.purpose == "personal_plan" and (
                not doc.published
                or not optional.plan(session, session.get(Employee, doc.owner_employee_id))
                or optional.plan(session, session.get(Employee, doc.owner_employee_id)).id != doc.id
            ):
                return False
        return True

    def deliver_one(self):
        db = self.runtime.db
        with db.transaction(write_lock=True) as session:
            for old in session.scalars(
                select(Outbox)
                .where(Outbox.status == "sending", Outbox.lease_until < utcnow())
                .with_for_update()
            ):
                old.status, old.last_error = "uncertain", "worker_crashed_during_delivery"
            item = session.scalar(
                select(Outbox)
                .outerjoin(Identity, Outbox.recipient_id == Identity.id)
                .where(
                    Outbox.status.in_(["pending", "retry"]),
                    Outbox.retry_at <= now(session),
                    or_(
                        Outbox.target_user_id.is_not(None),
                        Identity.max_user_id.is_not(None),
                        Identity.active.is_(False),
                    ),
                )
                .order_by(Outbox.created_at, Outbox.id)
                .limit(1)
                .with_for_update(of=Outbox, skip_locked=True)
            )
            if not item:
                return False
            if not self.extension_delivery_allowed(session, item):
                item.status, item.last_error = "cancelled", "extension_delivery_revoked"
                return True
            if not onboarding.delivery_allowed(session, item):
                item.status = "cancelled"
                return True
            if item.body.get("meeting_signal_id"):
                from .meetings import delivery_allowed

                if not delivery_allowed(session, item):
                    item.status = "cancelled"
                    return True
            actor = session.get(Identity, item.recipient_id) if item.recipient_id else None
            if item.body.get("manager_employee_id"):
                from .meetings import manager_for

                owner = session.get(Employee, item.body["manager_employee_id"])
                current_manager = manager_for(session, owner) if owner else None
                if not current_manager or current_manager.id != item.recipient_id:
                    item.status = "cancelled"
                    return True
            if item.body.get("support_employee_id"):
                from . import support
                from .models import Ticket

                owner = session.get(Employee, item.body["support_employee_id"])
                ticket = (
                    session.get(Ticket, item.body.get("support_ticket_id"))
                    if item.body.get("support_ticket_id")
                    else None
                )
                if (
                    not actor
                    or not owner
                    or not (
                        support.can_support(actor, owner) and ticket.recruiter_id == actor.id
                        if ticket
                        else support.can_support(actor, owner) and owner.recruiter_id == actor.id
                    )
                ):
                    item.status = "cancelled"
                    return True
            if item.body.get("digest_snapshot_id"):
                from .digests import can_read_snapshot
                from .models import DigestSnapshot

                snapshot = session.get(DigestSnapshot, item.body["digest_snapshot_id"])
                if (
                    not actor
                    or "hr_head" not in actor.roles
                    or not snapshot
                    or not can_read_snapshot(session, actor, snapshot)
                ):
                    item.status = "cancelled"
                    return True
            if actor and not actor.active:
                item.status = "cancelled"
                return True
            employee = (
                session.scalar(select(Employee).where(Employee.identity_id == actor.id)) if actor else None
            )
            if employee and (
                not employee.active or employee.status != "active" or "employee" not in actor.roles
            ):
                item.status = "cancelled"
                return True
            user_id = actor.max_user_id if actor else item.target_user_id
            from .routes import delivery_allowed as route_delivery_allowed

            if not route_delivery_allowed(session, item, employee):
                item.status = "cancelled"
                return True
            for doc_id in item.body.get("required_document_ids", []):
                doc = session.get(Document, doc_id)
                path = self.runtime.settings.data_dir / "documents" / doc.storage_name if doc else None
                if (
                    not doc
                    or not actor
                    or not documents.can_read(session, actor, doc)
                    or doc.status not in {"ready", "ready_stub"}
                    or not path.is_file()
                ):
                    item.status = "blocked_material"
                    item.last_error = "required_material_unavailable"
                    from .routes import block_delivery

                    block_delivery(session, item)
                    return True
            if not user_id:
                # Staff may not have bound MAX yet; retain the notification for later delivery.
                return False
            item.status, item.lease_owner = "sending", self.owner
            item.lease_until = utcnow() + timedelta(seconds=180)
            item.attempts += 1
            if "message_chunks" not in item.body:
                from .message_presentation import split_message

                legacy = item.body.get("next_chunk", 0) > 0
                chunks = (
                    (
                        [item.body["text"][i : i + 3500] for i in range(0, len(item.body["text"]), 3500)]
                        or [""]
                    )
                    if legacy
                    else split_message(item.body["text"])
                )
                item.body = {
                    **item.body,
                    "message_chunks": chunks,
                    "presentation_format": "legacy-plain" if legacy else "html-v1",
                }
            item_id, body = item.id, dict(item.body)
            media = []
            for doc_id in body.get("document_ids", []):
                doc = session.get(Document, doc_id)
                if not doc or not actor or not documents.can_read(session, actor, doc):
                    continue
                state = session.get(State, "max-media:" + doc.id)
                media.append((doc.id, doc.storage_name, state.value if state else None))
        try:
            attachments = []
            for doc_id, filename, cached in media:
                if not cached:
                    path = self.runtime.settings.data_dir / "documents" / filename
                    cached = self.runtime.transport.upload(
                        path, "image" if path.suffix.lower() in {".png", ".jpg", ".jpeg"} else "file"
                    )
                    with db.transaction(write_lock=True) as session:
                        state = session.get(State, "max-media:" + doc_id)
                        if not state:
                            session.add(State(key="max-media:" + doc_id, value=cached))
                attachments.append(cached)
            chunks = body["message_chunks"]
            for index in range(body.get("next_chunk", 0), len(chunks)):
                with db.transaction(write_lock=True) as session:
                    current = session.get(Outbox, item_id)
                    if current.status != "sending" or not self.extension_delivery_allowed(session, current):
                        current.status, current.last_error = "cancelled", "extension_delivery_revoked"
                        return True
                message = {
                    "text": chunks[index],
                    "presentation_format": body["presentation_format"],
                    "buttons": body.get("buttons", []) if index == len(chunks) - 1 else [],
                }
                provider_id = self.runtime.transport.send(
                    user_id, message, attachments if index == len(chunks) - 1 else []
                )
                with db.transaction(write_lock=True) as session:
                    item = session.get(Outbox, item_id)
                    item.body = {**item.body, "next_chunk": index + 1}
                    item.provider_message_id = provider_id
                    if not self.extension_delivery_allowed(session, item):
                        item.status, item.last_error = "uncertain", "source_changed_during_delivery"
                        return True
                if self.runtime.settings.transport == "max":
                    time.sleep(0.55)  # Single delivery worker: below 2 messages/s per dialogue and 30 rps.
            with db.transaction(write_lock=True) as session:
                session.get(Outbox, item_id).status = "sent"
                if body.get("meeting_signal_id"):
                    from .models import MeetingSignal

                    signal = session.get(MeetingSignal, body["meeting_signal_id"])
                    if signal:
                        signal.status = "delivered"
        except Exception as exc:
            with db.transaction(write_lock=True) as session:
                item = session.get(Outbox, item_id)
                uncertain = not isinstance(exc, DeliveryError) or exc.uncertain
                item.last_error = exc.code if isinstance(exc, DeliveryError) else type(exc).__name__
                if uncertain:
                    item.status = "uncertain"
                elif exc.retryable and item.attempts < self.runtime.settings.max_job_attempts:
                    item.status = "retry"
                    item.retry_at = now(session) + timedelta(seconds=min(300, 2**item.attempts))
                else:
                    item.status = "failed"
                log.warning("delivery_failed id=%s code=%s", item_id, item.last_error)
        return True

    def tick(self, limit=100):
        self.schedule()
        jobs = 0
        while jobs < limit and self.process_one():
            jobs += 1
        deliveries = 0
        while deliveries < limit and self.deliver_one():
            deliveries += 1
        with self.runtime.db.transaction(write_lock=True) as session:
            session.merge(
                State(key="worker_heartbeat", value={"at": utcnow().isoformat(), "worker": self.owner})
            )
        return {"jobs_processed": jobs, "deliveries_processed": deliveries}

    def run(self):
        while True:
            self.tick()
            time.sleep(self.runtime.settings.worker_interval_seconds)


def poll(runtime):
    if runtime.settings.environment == "production":
        raise ValueError("Long polling is only supported for development by MAX")
    if runtime.settings.transport != "max":
        raise ValueError("Set transport=max to poll real events")
    while True:
        try:
            with runtime.db.transaction() as session:
                marker = session.get(State, "max_marker")
                value = marker.value.get("marker") if marker else None
            response = runtime.transport.get_updates(value)
            with runtime.db.transaction(write_lock=True) as session:
                for update in response.get("updates", []):
                    event = normalize_update(update)
                    if event:
                        ingest(session, event)
                marker = session.get(State, "max_marker")
                if not marker:
                    marker = State(key="max_marker", value={})
                    session.add(marker)
                marker.value = {"marker": response.get("marker")}
        except Exception as exc:
            response = getattr(exc, "response", None)
            status = response.status_code if response is not None else None
            log.warning("poll_failed error=%s http_status=%s", type(exc).__name__, status)
            time.sleep(15 if status in {429, 502, 503, 504} else 5)
