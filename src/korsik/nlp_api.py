"""Candidate NLP event contract; a separate adapter preserves the existing ML v1 port."""

import hashlib
import hmac
import json
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import Field
from sqlalchemy import select

from .calendar import now
from .dependencies import Session, runtime_dependency
from .ml import AnalysisResult
from .models import Employee, FeedbackAnalysis, NlpEvent, SurveyAnswer, SurveyRun
from .schemas import StrictModel

router = APIRouter(prefix="/api/integrations/nlp", tags=["NLP event adapter"])


class AnalysisEvent(StrictModel):
    schema_version: Literal["1"] = "1"
    event_id: str = Field(min_length=1, max_length=150)
    answer_id: str
    result: AnalysisResult
    manager_signal: Literal["none", "meeting_recommended"] = "none"


@router.post("/events", status_code=202)
def receive(
    data: AnalysisEvent,
    session: Session,
    authorization: Annotated[str | None, Header()] = None,
    runtime=Depends(runtime_dependency),
):
    secret = runtime.settings.ml_token
    if not secret:
        raise HTTPException(503, "NLP callback не настроен")
    if not authorization or not hmac.compare_digest(authorization, "Bearer " + secret):
        raise HTTPException(401, "Неверный ключ интеграции")
    payload = data.model_dump(mode="json")
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    existing = session.get(NlpEvent, data.event_id)
    if existing:
        if existing.payload_hash != fingerprint:
            raise HTTPException(409, "event_id уже использован с другим содержимым")
        return {"accepted": True, "duplicate": True}
    answer = session.get(SurveyAnswer, data.answer_id)
    if not answer:
        raise HTTPException(404, "Ответ не найден")
    run = session.get(SurveyRun, answer.run_id)
    employee = session.get(Employee, run.employee_id)
    session.add(
        NlpEvent(
            event_id=data.event_id,
            answer_id=answer.id,
            payload=payload,
            payload_hash=fingerprint,
            created_at=now(session),
        )
    )
    analysis = session.scalar(select(FeedbackAnalysis).where(FeedbackAnalysis.answer_id == answer.id))
    if not analysis:
        analysis = FeedbackAnalysis(
            answer_id=answer.id,
            employee_id=employee.id,
            processor_version=data.result.processor_version,
            result=data.result.model_dump(),
            created_at=now(session),
        )
        session.add(analysis)
        session.flush()
    else:
        legacy_id = "previous:" + analysis.id
        if not session.get(NlpEvent, legacy_id):
            original = {"result": analysis.result, "processor_version": analysis.processor_version}
            session.add(
                NlpEvent(
                    event_id=legacy_id,
                    answer_id=answer.id,
                    payload=original,
                    payload_hash=hashlib.sha256(json.dumps(original, sort_keys=True).encode()).hexdigest(),
                    created_at=analysis.created_at,
                )
            )
        analysis.result = data.result.model_dump()
        analysis.processor_version = data.result.processor_version
        analysis.created_at = now(session)
    from .feedback import apply_policy

    apply_policy(session, employee, analysis, data.result, data.manager_signal == "meeting_recommended")
    return {"accepted": True, "duplicate": False, "analysis_id": analysis.id}
