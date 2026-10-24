"""Versioned port to the team's ML service. No model or RAG implementation lives here."""

from typing import Literal, Protocol

import httpx
from pydantic import Field

from .schemas import StrictModel


class HistoryMessage(StrictModel):
    role: Literal["user", "assistant", "hr"]
    text: str = Field(max_length=2000)


class AccessContext(StrictModel):
    branch_code: str
    category: str


class StepContext(StrictModel):
    stage: str
    survey_id: str | None = None
    question_index: int | None = Field(default=None, ge=0)


class AllowedDocument(StrictModel):
    id: str
    title: str
    version: int = Field(ge=1)


class AnswerRequest(StrictModel):
    request_id: str
    employee_id: str
    question: str = Field(min_length=1, max_length=15000)
    history: list[HistoryMessage] = Field(default_factory=list, max_length=8)
    step_context: StepContext
    access: AccessContext
    allowed_documents: list[AllowedDocument] = Field(default_factory=list)
    address_style: Literal["formal", "informal"]


class AnalysisRequest(StrictModel):
    request_id: str
    employee_id: str
    question: str
    text: str = Field(min_length=1, max_length=15000)
    answer: dict
    access: AccessContext


class Source(StrictModel):
    document_id: str
    version: int = Field(ge=1)
    page: int | None = Field(default=None, ge=1)


class AnswerResult(StrictModel):
    status: Literal["answered", "not_found", "clarification"]
    answer: str = Field(min_length=1, max_length=12000)
    sources: list[Source] = Field(default_factory=list, max_length=20)
    topics: list[str] = Field(default_factory=list, max_length=20)
    needs_hr: bool = False
    processor_version: str = Field(min_length=1, max_length=100)


class AnalysisResult(StrictModel):
    sentiment: Literal["positive", "neutral", "negative"]
    risk: Literal["none", "low", "medium", "high"]
    topics: list[str] = Field(default_factory=list, max_length=20)
    explanation: str = Field(max_length=3000)
    needs_hr: bool = False
    processor_version: str = Field(min_length=1, max_length=100)


class IndexResult(StrictModel):
    status: Literal["processing", "ready", "failed"]
    external_id: str
    processor_version: str


class MLUnavailable(Exception):
    pass


class MLPort(Protocol):
    def answer(self, payload: dict) -> AnswerResult: ...
    def analyze(self, payload: dict) -> AnalysisResult: ...
    def index(self, document, path) -> IndexResult: ...
    def index_status(self, external_id: str) -> IndexResult: ...
    def close(self): ...


class StubML:
    """Deterministic fixtures, intentionally labelled. Never pretends to read company documents."""

    def answer(self, payload):
        demo = next((d for d in payload["allowed_documents"] if d["title"].startswith("ДЕМО:")), None)
        if demo and "портал" in payload["question"].lower():
            return AnswerResult(
                status="answered",
                answer="[ДЕМО ML] Для доступа к учебному порталу обратитесь "
                "к закреплённому рекрутеру. Это тестовая инструкция, а не регламент ПОЛИПЛАСТИКА.",
                sources=[Source(document_id=demo["id"], version=demo["version"], page=1)],
                topics=["access"],
                processor_version="stub/1",
            )
        return AnswerResult(
            status="not_found",
            answer="Работает ML-заглушка. Подтверждённого ответа нет.",
            processor_version="stub/1",
        )

    def analyze(self, payload):
        flagged = "DEMO:RED_FLAG" in payload["text"]
        return AnalysisResult(
            sentiment="negative" if flagged else "neutral",
            risk="high" if flagged else "none",
            topics=["demo_risk"] if flagged else [],
            explanation="Демонстрационный сигнал по маркеру DEMO:RED_FLAG."
            if flagged
            else "Заглушка не анализирует настроение реального сотрудника.",
            needs_hr=flagged,
            processor_version="stub/1",
        )

    def index(self, document, path):
        return IndexResult(status="ready", external_id=document.id, processor_version="stub/1")

    def index_status(self, external_id):
        return IndexResult(status="ready", external_id=external_id, processor_version="stub/1")

    def close(self):
        pass


class HTTPML:
    def __init__(self, settings, client=None):
        self.client = client or httpx.Client(
            base_url=settings.ml_base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {settings.ml_token}"},
            timeout=httpx.Timeout(settings.ml_timeout_seconds, connect=5),
            # Локальный RAG на 127.0.0.1 не должен ходить через системный прокси.
            trust_env=False,
        )

    def _post(self, path, payload, model):
        try:
            response = self.client.post(
                path, json=payload, headers={"Idempotency-Key": payload["request_id"]}
            )
            response.raise_for_status()
            return model.model_validate(response.json())
        except (httpx.HTTPError, ValueError) as exc:
            raise MLUnavailable(type(exc).__name__) from exc

    def answer(self, payload):
        return self._post("/v1/answer", payload, AnswerResult)

    def analyze(self, payload):
        return self._post("/v1/analyze", payload, AnalysisResult)

    def index(self, document, path):
        import json

        try:
            with path.open("rb") as stream:
                response = self.client.post(
                    "/v1/documents",
                    files={"file": (document.original_name, stream)},
                    data={
                        "metadata": json.dumps(
                            {
                                "document_id": document.id,
                                "version": document.version,
                                "title": document.title,
                                "sha256": document.sha256,
                                "branch_codes": document.branch_codes,
                                "categories": document.categories,
                            }
                        )
                    },
                    headers={"Idempotency-Key": f"document:{document.id}:{document.version}"},
                )
            response.raise_for_status()
            return IndexResult.model_validate(response.json())
        except (httpx.HTTPError, ValueError) as exc:
            raise MLUnavailable(type(exc).__name__) from exc

    def index_status(self, external_id):
        try:
            from urllib.parse import quote

            response = self.client.get(f"/v1/documents/{quote(external_id, safe='')}")
            response.raise_for_status()
            return IndexResult.model_validate(response.json())
        except (httpx.HTTPError, ValueError) as exc:
            raise MLUnavailable(type(exc).__name__) from exc

    def close(self):
        self.client.close()
