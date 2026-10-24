from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator, model_validator

from .admin_schemas import AudienceInput
from .schemas import StrictModel


class AwareInput(StrictModel):
    @field_validator("starts_at", "due_at", check_fields=False)
    @classmethod
    def aware(cls, value):
        if value and value.tzinfo is None:
            raise ValueError("Укажите время с часовым поясом")
        return value


class PlannedEventInput(AwareInput):
    kind: Literal["event", "meeting"]
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=12000)
    branch_code: str
    starts_at: datetime | None = None
    timezone: str = Field(default="Europe/Moscow", max_length=100)
    location: str = Field(default="", max_length=1024)
    audience: AudienceInput
    document_ids: list[str] = Field(default_factory=list, max_length=10)
    status: Literal["draft", "published", "scheduled", "cancelled", "held", "archived"] = "draft"
    revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_kind(self):
        try:
            ZoneInfo(self.timezone)
        except (KeyError, ValueError) as exc:
            raise ValueError("Укажите существующий часовой пояс IANA") from exc
        if self.kind == "meeting" and not self.starts_at:
            raise ValueError("Для встречи укажите дату и время")
        allowed = (
            {"draft", "published", "archived"}
            if self.kind == "event"
            else {"draft", "scheduled", "cancelled", "held"}
        )
        if self.status not in allowed:
            raise ValueError("Недопустимый статус для этого вида записи")
        return self


class AnnouncementInput(AwareInput):
    revision: int = Field(ge=1)
    due_at: datetime | None = None


class PlanDeliveryInput(AwareInput):
    request_key: str = Field(min_length=1, max_length=100)
    due_at: datetime | None = None
    third_workday: bool = False

    @model_validator(mode="after")
    def one_schedule(self):
        if self.third_workday and self.due_at:
            raise ValueError("Выберите дату отправки или правило третьего дня")
        return self


class GrantInput(StrictModel):
    issuer_id: str
    assignee_id: str
    branch_code: str
    active: bool = True


class TaskInput(AwareInput):
    mode: Literal["real", "demo"] = "real"
    assignee_id: str
    branch_code: str
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=12000)
    expected_result: str = Field(default="", max_length=4000)
    due_at: datetime | None = None
    source_insight_id: str | None = None
    source_report_id: str | None = None
    replaces_task_id: str | None = None
    request_key: str = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def source_pair(self):
        if bool(self.source_insight_id) != bool(self.source_report_id):
            raise ValueError("Кандидат и снимок отчёта указываются вместе")
        return self


class TaskCommand(AwareInput):
    action: Literal[
        "accept", "block", "resume", "complete", "clarify", "answer_blocker", "update", "reopen", "cancel"
    ]
    revision: int = Field(ge=1)
    request_key: str = Field(min_length=1, max_length=100)
    text: str = Field(default="", max_length=12000)
    result_kind: Literal["knowledge_updated", "process_adjusted", "no_change_needed"] | None = None
    document_id: str | None = None
    document_version: int | None = Field(default=None, ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, min_length=1, max_length=12000)
    expected_result: str | None = Field(default=None, max_length=4000)
    due_at: datetime | None = None


class ReadInput(StrictModel):
    through_seq: int = Field(ge=0)


class ProcessRule(StrictModel):
    survey_id: str
    survey_version: str = Field(pattern=r"^[a-f0-9]{64}$")
    question_id: str
    topic: str = Field(min_length=1, max_length=100)
    below: int | None = None
    negative_options: list[str] = Field(default_factory=list)


class ImprovementPolicy(StrictModel):
    enabled: bool = False
    mode: Literal["demo", "real"] = "demo"
    window_days: int = Field(default=7, ge=1, le=30)
    min_employees: int = Field(default=3, ge=3, le=100)
    approved_by: list[str] = Field(default_factory=list, max_length=20)
    approved_topics: list[str] = Field(
        default_factory=lambda: ["documents", "access", "hr", "manager_support"]
    )
    process_topics: list[str] = Field(default_factory=list, max_length=100)
    process_rules: list[ProcessRule] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def approval(self):
        if self.enabled and self.mode == "real" and (not self.approved_by or not self.approved_topics):
            raise ValueError("Для реального отчёта нужны согласовавшие и перечень тем")
        return self
