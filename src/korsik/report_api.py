"""Authenticated report selection, preview and short-lived native MAX downloads."""

import hashlib
import hmac
import time

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from . import reporting
from .access import require
from .dependencies import Actor, Session, runtime_dependency
from .models import ExportFile, Identity

router = APIRouter(prefix="/api/reports", tags=["Survey reports"])


def signature(settings, id, expires):
    return hmac.new(
        settings.bootstrap_token.encode(), f"export:{id}:{expires}".encode(), hashlib.sha256
    ).hexdigest()


@router.get("/surveys")
def surveys(session: Session, actor: Actor):
    return reporting.catalogue(session, actor)


@router.post("/preview")
def preview(data: reporting.ReportInput, session: Session, actor: Actor):
    report = reporting.snapshot(session, actor, data)
    return {k: v for k, v in report.items() if k != "rows"} | {
        "preview_hash": reporting.preview_hash(report),
        "sample": report["rows"][:20],
        "truncated_preview": len(report["rows"]) > 20,
    }


@router.post("/files", status_code=201)
def create(data: reporting.ReportInput, session: Session, actor: Actor, runtime=Depends(runtime_dependency)):
    item = reporting.create_file(session, actor, runtime.settings, data)
    expires = int(time.time()) + 60
    path = f"/api/reports/files/{item.id}?expires={expires}&ticket={signature(runtime.settings, item.id, expires)}"
    base = runtime.settings.webapp_public_url
    if base:
        from urllib.parse import urlsplit

        value = urlsplit(base)
        path = f"{value.scheme}://{value.netloc}" + path
    return {
        "id": item.id,
        "filename": item.filename,
        "url": path,
        "expires_at": item.expires_at,
        "rows": item.row_count,
    }


@router.get("/files/{file_id}")
def download(
    file_id: str,
    session: Session,
    expires: int = Query(),
    ticket: str = Query(),
    runtime=Depends(runtime_dependency),
):
    require(
        0 <= expires - int(time.time()) <= 60
        and hmac.compare_digest(signature(runtime.settings, file_id, expires), ticket),
        "Ссылка выгрузки истекла",
    )
    item = session.get(ExportFile, file_id)
    if not item:
        raise HTTPException(404, "Выгрузка не найдена")
    actor = session.get(Identity, item.actor_id)
    require(reporting.can_download(session, actor, item), "Доступ к данным изменился или выгрузка истекла")
    path = runtime.settings.data_dir / "exports" / item.id
    if not path.is_file():
        raise HTTPException(404, "Файл удалён. Сформируйте выгрузку заново")
    mime = (
        "text/csv; charset=utf-8"
        if item.filename.endswith("csv")
        else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    return FileResponse(path, filename=item.filename, media_type=mime, headers={"Cache-Control": "no-store"})
