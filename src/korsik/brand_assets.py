"""Original company images, matched by the image numbers in the preserved Excel cells."""

import hashlib
import json
from pathlib import Path

from sqlalchemy import select

from . import access
from .calendar import now
from .models import Document, Material

DIRECTORY = Path(__file__).parent / "web" / "brand"


def manifest():
    return json.loads((DIRECTORY / "manifest.json").read_text(encoding="utf-8"))


def install(session, actor=None):
    """Fill only empty references; never replace HR choices or activate scenario rows."""
    data_dir = session.info.get("data_dir")
    if not data_dir:
        raise ValueError("Document storage directory is not configured")
    if actor:
        access.require(actor.active and access.is_hr(actor))
    catalogue = manifest()
    by_key = {b["id"]: b for b in catalogue["bindings"]}
    assets = {a["number"]: a for a in catalogue["assets"]}
    installed, preserved = [], []
    for material in session.scalars(select(Material).order_by(Material.key)):
        binding = by_key.get(material.key.split(":", 1)[0])
        if not binding or actor and not access.scope_contains(actor, material.branch_codes):
            continue
        if material.document_id or material.url:
            preserved.append(material.key)
            continue
        asset = assets[binding["image_number"]]
        content = (DIRECTORY / asset["file"]).read_bytes()
        if hashlib.sha256(content).hexdigest() != asset["sha256"]:
            raise ValueError("Company image checksum mismatch")
        doc_id = "brand-" + str(asset["number"]).zfill(2) + "-" + asset["sha256"][:22]
        doc = session.get(Document, doc_id)
        if not doc:
            doc = Document(
                id=doc_id, title=f"Корсик · Изображение {asset['number']}",
                original_name=asset["file"], storage_name=doc_id + ".jpg", sha256=asset["sha256"],
                branch_codes=["*"], categories=[], version=1, status="ready", published=True,
                purpose="route_image", processor_version="company-assets/1", family_id=doc_id,
                created_at=now(session),
            )
            session.add(doc)
            session.flush()
        folder = Path(data_dir) / "documents"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / doc.storage_name
        if not path.exists():
            path.write_bytes(content)
        elif hashlib.sha256(path.read_bytes()).hexdigest() != asset["sha256"]:
            raise ValueError("Stored company image checksum mismatch")
        material.document_id = doc.id
        material.revision += 1
        installed.append(material.key)
    return {"installed": installed, "preserved": preserved, "missing": catalogue["missing"]}
