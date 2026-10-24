"""HR configuration of material readiness and v0.4 source-backed route."""

from zoneinfo import available_timezones

from fastapi import APIRouter, HTTPException
from pydantic import Field
from sqlalchemy import select

from . import access, admin_service, documents, profile_v04, routes, scenarios
from .dependencies import Actor, Admin, Session
from .models import Branch, Document, Employee, Material, Scenario, ScheduledStep
from .schemas import StrictModel
from .templates import TOKENS

router = APIRouter(prefix="/api/admin", tags=["Route configuration"])


class MaterialInput(StrictModel):
    document_id: str | None = None
    url: str | None = Field(default=None, max_length=1024)
    revision: int = Field(ge=1)
    owner: str | None = Field(default=None, max_length=200)


class BranchRouteInput(StrictModel):
    variant: str
    revision: int = Field(ge=1)


@router.post("/branches/{code}/route-v04")
def branch_route(code: str, data: BranchRouteInput, session: Session, actor: Admin):
    """Derive a self-contained configuration for a new site without changing Python code."""
    from copy import deepcopy

    from sqlalchemy import func

    admin_service.check_scope(actor, [code])
    branch = session.get(Branch, code)
    if not branch:
        raise HTTPException(404, "Площадка не найдена")
    admin_service.revision_check(branch, data.revision)
    if data.variant not in {"GrPP", "GTP", "Luzhniki"}:
        raise HTTPException(422, "Выберите образец маршрута")
    profile_v04.install(session)
    definition = deepcopy(profile_v04.compile_profile())
    name = "Исходный маршрут v0.4 · " + code
    definition["name"] = name
    definition["version"] = (
        session.scalar(select(func.max(Scenario.version)).where(Scenario.name == name)) or 0
    ) + 1
    steps = []
    for step in definition["steps"]:
        if step["branch_codes"] and data.variant not in step["branch_codes"]:
            continue
        step["branch_codes"] = [code]
        nodes = []
        for node in step["actions"]:
            if node.get("branch_codes") and data.variant not in node["branch_codes"]:
                continue
            node["branch_codes"] = [code]
            if node.get("material_key"):
                original = session.get(Material, node["material_key"])
                key = original.key + ":" + code
                if not session.get(Material, key):
                    doc = session.get(Document, original.document_id) if original.document_id else None
                    session.add(
                        Material(
                            key=key,
                            title=(original.title + " · " + branch.name)[:300],
                            source_ref=original.source_ref,
                            branch_codes=[code],
                            required=original.required,
                            owner=actor.name,
                            document_id=doc.id
                            if doc and ("*" in doc.branch_codes or code in doc.branch_codes)
                            else None,
                            url=original.url,
                        )
                    )
                    session.flush()
                node["material_key"] = key
            nodes.append(node)
        step["actions"] = nodes
        steps.append(step)
    definition["steps"] = steps
    from .schemas import ScenarioDefinition

    definition = ScenarioDefinition.model_validate(definition).model_dump(mode="json")
    scenario = Scenario(name=name, version=definition["version"], definition=definition, branch_codes=[code])
    session.add(scenario)
    session.flush()
    branch.scenario_id = scenario.id
    branch.revision += 1
    for employee in session.scalars(
        select(Employee).where(Employee.branch_code == code, Employee.active.is_(True))
    ):
        scenarios.rebuild_schedule(session, employee)
    admin_service.audit(session, actor, "route.derive", branch.code)
    return admin_service.branch_dto(session, branch)


@router.get("/reference-data")
def reference_data(session: Session, actor: Actor):
    access.require(access.can_use_workspace(actor))
    return {
        "timezones": sorted(available_timezones()),
        "placeholders": [{"key": "{" + key + "}", "description": value} for key, value in TOKENS.items()],
    }


@router.post("/route-v04/install")
def install(session: Session, actor: Admin):
    access.require(access.is_hr(actor) and "*" in actor.branch_codes)
    scenario = profile_v04.install(session)
    admin_service.audit(session, actor, "route.install", scenario.id)
    return {"id": scenario.id, "steps": len(scenario.definition["steps"]), "source_rows": 55}


@router.get("/materials")
def list_materials(session: Session, actor: Admin):
    admin_service.require_hr(actor)
    return [
        {
            "key": m.key,
            "title": m.title,
            "source_ref": m.source_ref,
            "branch_codes": m.branch_codes,
            "required": m.required,
            "document_id": m.document_id,
            "url": m.url,
            "revision": m.revision,
            "owner": m.owner,
            "can_edit": access.scope_contains(actor, m.branch_codes),
            "ready": bool(
                m.url
                and not m.key.startswith("MAT-")
                and m.key.split(":", 1)[0] not in {"RES-R026", "RES-R027", "RES-R028"}
                or m.document_id
                and (doc := session.get(Document, m.document_id))
                and doc.active
                and doc.published
                and doc.status in {"ready", "ready_stub"}
                and routes.document_matches(m, doc)
                and (
                    not session.info.get("data_dir")
                    or (session.info["data_dir"] / "documents" / doc.storage_name).is_file()
                )
            ),
        }
        for m in session.scalars(select(Material).order_by(Material.source_ref, Material.key))
        if admin_service.visible_scoped(actor, m.branch_codes)
    ]


@router.put("/materials/{key}")
def save_material(key: str, data: MaterialInput, session: Session, actor: Admin):
    m = session.get(Material, key)
    if not m:
        raise HTTPException(404, "Материал не найден")
    admin_service.check_scope(actor, m.branch_codes)
    admin_service.revision_check(m, data.revision)
    if data.document_id:
        doc = session.get(Document, data.document_id)
        access.require(bool(doc and documents.can_read(session, actor, doc)))
        if not routes.document_matches(m, doc):
            raise HTTPException(422, "Для изображения выберите PNG/JPG; для питания — PDF")
        if not ("*" in doc.branch_codes or set(m.branch_codes) <= set(doc.branch_codes)):
            raise HTTPException(422, "Область документа не покрывает материал")
        if key.split(":", 1)[0] in {
            "RES-R026",
            "RES-R027",
            "RES-R028",
        } and not doc.original_name.lower().endswith(".pdf"):
            raise HTTPException(422, "Для питания требуется PDF-файл")
    if data.url:
        if key.startswith("MAT-"):
            raise HTTPException(422, "Для изображения требуется загруженный PNG/JPG, а не текстовая ссылка")
        from urllib.parse import urlsplit

        parsed = urlsplit(data.url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
            raise HTTPException(422, "Укажите HTTP(S)-ссылку без секретов")
        if key.split(":", 1)[0] in {"RES-R026", "RES-R027", "RES-R028"}:
            raise HTTPException(422, "Загрузите PDF через раздел «Документы» и выберите его")
    m.document_id = data.document_id
    m.url = data.url
    m.owner = data.owner or actor.name
    m.revision += 1
    admin_service.audit(session, actor, "material.update", m.key)
    return {"ok": True, "revision": m.revision}


@router.get("/route-readiness")
def readiness(session: Session, actor: Admin):
    admin_service.require_hr(actor)
    materials = list_materials(session, actor)
    return {
        "materials": materials,
        "missing_required": sum(m["required"] and not m["ready"] for m in materials),
        "blocked": [
            {
                "id": s.id,
                "employee_id": e.id,
                "employee_name": e.name,
                "step": s.step_key,
                "status": s.status,
                "error": s.last_error,
            }
            for s in session.scalars(
                select(ScheduledStep).where(ScheduledStep.status.in_(["waiting_material", "late_review"]))
            )
            if (e := session.get(Employee, s.employee_id)) and access.can_support(actor, e)
        ],
    }
