import hashlib
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select

from .access import in_scope
from .calendar import now
from .models import Document, Employee, uid
from .queue import enqueue

ALLOWED_EXTENSIONS = {".pdf", ".txt", ".md", ".docx", ".pptx", ".png", ".jpg", ".jpeg"}


def employee_can_read(employee, document):
    return (
        document.active
        and document.published
        and (document.purpose != "personal_plan" or document.owner_employee_id == employee.id)
        and ("*" in document.branch_codes or employee.branch_code in document.branch_codes)
        and (not document.categories or employee.category in document.categories)
    )


def can_read(session, actor, document):
    if not actor.active or not document.active:
        return False
    if document.purpose == "personal_plan":
        from .access import can_view_employee

        owner = session.get(Employee, document.owner_employee_id)
        return bool(owner and can_view_employee(actor, owner))
    if set(actor.roles) & {"hr", "hr_head"}:
        # A global document is visible to scoped HR; creation of global documents requires global scope.
        return "*" in document.branch_codes or any(in_scope(actor, b) for b in document.branch_codes)
    employee = session.scalar(select(Employee).where(Employee.identity_id == actor.id))
    return bool(employee and employee_can_read(employee, document))


def can_edit(actor, document):
    if document.purpose == "personal_plan":
        return actor.active and bool(set(actor.roles) & {"hr", "hr_head"}) and all(
            in_scope(actor, b) for b in document.branch_codes
        )
    return bool(set(actor.roles) & {"hr", "hr_head"}) and all(
        in_scope(actor, b) for b in document.branch_codes
    )


def allowed_documents(session, employee):
    return [
        d
        for d in session.scalars(
            select(Document).where(
                Document.active.is_(True), Document.purpose == "knowledge",
                Document.status.in_(["ready", "ready_stub"]),
            )
        )
        if employee_can_read(employee, d)
    ]


def store(
    session,
    settings,
    title,
    filename,
    content,
    branches,
    categories,
    version=1,
    *,
    previous=None,
    auto_publish=True,
    purpose="knowledge",
    owner_employee_id=None,
    created_by=None,
):
    if previous:
        purpose = previous.purpose
        owner_employee_id = previous.owner_employee_id
        created_by = created_by or previous.created_by
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS or len(content) > settings.max_upload_bytes or not content:
        raise HTTPException(422, "Недопустимый формат, размер или пустой файл")
    if suffix == ".pdf" and not content.startswith(b"%PDF-"):
        raise HTTPException(422, "Файл не является PDF")
    if suffix in {".docx", ".pptx"} and not content.startswith(b"PK"):
        raise HTTPException(422, "Некорректный формат Office")
    doc_id = uid()
    storage_name = doc_id + suffix
    directory = settings.data_dir / "documents"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / storage_name).write_bytes(content)
    doc = Document(
        id=doc_id,
        title=title,
        original_name=Path(filename).name,
        storage_name=storage_name,
        sha256=hashlib.sha256(content).hexdigest(),
        branch_codes=branches,
        categories=categories,
        version=version,
        created_at=now(session),
        family_id=(previous.family_id or previous.id) if previous else doc_id,
        replaces_id=previous.id if previous else None,
        published=False,
        auto_publish=auto_publish,
        purpose=purpose,
        owner_employee_id=owner_employee_id,
        created_by=created_by,
    )
    session.add(doc)
    session.flush()
    if purpose == "knowledge":
        enqueue(session, "index_document", {"document_id": doc.id}, f"index:{doc.id}", now(session))
    else:
        doc.status = "ready"
        publish(session, doc)
    return doc


def public_document(document):
    return {
        "id": document.id,
        "title": document.title,
        "filename": document.original_name,
        "branch_codes": document.branch_codes,
        "categories": document.categories,
        "version": document.version,
        "status": document.status,
        "processor_version": document.processor_version,
        "active": document.active,
        "published": document.published,
        "auto_publish": document.auto_publish,
        "family_id": document.family_id or document.id,
        "replaces_id": document.replaces_id,
        "last_error": document.last_error,
        "revision": document.revision,
        "purpose": document.purpose,
        "owner_employee_id": document.owner_employee_id,
        "created_by": document.created_by,
    }


def publish(session, document):
    """Atomically switch the active version after processing; lock the family root."""
    if not document.active or document.status not in {"ready", "ready_stub"}:
        raise HTTPException(409, "Опубликовать можно только обработанный действующий документ")
    root = document.family_id or document.id
    session.scalar(select(Document).where(Document.id == root).with_for_update())
    for other in session.scalars(
        select(Document)
        .where(Document.family_id == root)
        .with_for_update()
        .execution_options(populate_existing=True)
    ):
        if other.id != document.id and other.published:
            other.published = False
            other.revision += 1
    document.published = True
    document.revision += 1
    session.flush()
    return document
