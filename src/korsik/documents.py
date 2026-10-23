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
        and ("*" in document.branch_codes or employee.branch_code in document.branch_codes)
        and (not document.categories or employee.category in document.categories)
    )


def can_read(session, actor, document):
    if not document.active:
        return False
    if set(actor.roles) & {"hr", "hr_head"}:
        # A global document is visible to scoped HR; creation of global documents requires global scope.
        return "*" in document.branch_codes or any(in_scope(actor, b) for b in document.branch_codes)
    employee = session.scalar(select(Employee).where(Employee.identity_id == actor.id))
    return bool(employee and employee_can_read(employee, document))


def can_edit(actor, document):
    return bool(set(actor.roles) & {"hr", "hr_head"}) and all(
        in_scope(actor, b) for b in document.branch_codes
    )


def allowed_documents(session, employee):
    return [
        d
        for d in session.scalars(
            select(Document).where(Document.active.is_(True), Document.status.in_(["ready", "ready_stub"]))
        )
        if employee_can_read(employee, d)
    ]


def store(session, settings, title, filename, content, branches, categories, version=1):
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
    )
    session.add(doc)
    session.flush()
    enqueue(session, "index_document", {"document_id": doc.id}, f"index:{doc.id}", now(session))
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
    }
