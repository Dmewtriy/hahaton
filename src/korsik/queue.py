from sqlalchemy import select

from .db import utcnow
from .models import Job, Outbox


def enqueue(session, kind, payload, key, due_at):
    existing = session.scalar(select(Job).where(Job.dedupe_key == key))
    if existing:
        return existing
    job = Job(kind=kind, payload=payload, dedupe_key=key, due_at=due_at)
    session.add(job)
    session.flush()
    return job


def send(session, recipient_id, text, key, at, *, buttons=None, document_ids=None):
    existing = session.scalar(select(Outbox).where(Outbox.dedupe_key == key))
    if existing:
        return existing
    item = Outbox(
        recipient_id=recipient_id,
        dedupe_key=key,
        body={"text": text, "buttons": buttons or [], "document_ids": document_ids or []},
        retry_at=at,
        created_at=utcnow(),
    )
    session.add(item)
    session.flush()
    return item
