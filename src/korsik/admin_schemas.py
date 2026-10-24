from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .schemas import BranchInput, EmployeeCreate, Question, ScenarioDefinition, StrictModel


class RevisionInput(StrictModel):
    revision: int | None = Field(default=None, ge=1)


class EmployeeInput(EmployeeCreate):
    department_id: str | None = None
    active: bool = True
    revision: int | None = Field(default=None, ge=1)


class BranchEdit(BranchInput):
    revision: int | None = Field(default=None, ge=1)
    resource_labels: dict[str, str] = Field(default_factory=dict)


class DepartmentInput(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    branch_code: str
    manager_id: str | None = None
    revision: int | None = Field(default=None, ge=1)


class DocumentInput(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    branch_codes: list[str] = Field(min_length=1)
    categories: list[str] = Field(default_factory=list)
    revision: int | None = Field(default=None, ge=1)


class SurveyInput(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=3000)
    branch_codes: list[str] = Field(min_length=1)
    questions: list[Question] = Field(min_length=1, max_length=100)
    reminder_days: list[int] = Field(default_factory=lambda: [1, 2])
    revision: int | None = Field(default=None, ge=1)


class ScenarioEdit(ScenarioDefinition):
    branch_codes: list[str] = Field(min_length=1)
    revision: int | None = Field(default=None, ge=1)


class AudienceInput(StrictModel):
    mode: Literal["all", "branches", "departments", "employees"] = "all"
    branch_codes: list[str] = Field(default_factory=list)
    department_ids: list[str] = Field(default_factory=list)
    employee_ids: list[str] = Field(default_factory=list, max_length=10000)
    categories: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def required_selection(self):
        fields = {
            "branches": self.branch_codes,
            "departments": self.department_ids,
            "employees": self.employee_ids,
        }
        if self.mode != "all" and not fields[self.mode]:
            raise ValueError("Выберите получателей рассылки")
        return self


class CampaignInput(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    kind: Literal["message", "survey"]
    text: str = Field(min_length=1, max_length=12000)
    document_ids: list[str] = Field(default_factory=list, max_length=10)
    template_id: str | None = None
    audience: AudienceInput
    due_at: datetime | None = None
    revision: int | None = Field(default=None, ge=1)

    @field_validator("due_at")
    @classmethod
    def aware_datetime(cls, value):
        if value and value.tzinfo is None:
            raise ValueError("Укажите часовой пояс времени отправки")
        return value

    @model_validator(mode="after")
    def survey_required(self):
        if self.kind == "survey" and not self.template_id:
            raise ValueError("Выберите опросник")
        return self


class TicketInput(StrictModel):
    reason: str = Field(min_length=1, max_length=200)
    recruiter_id: str


class PublishInput(StrictModel):
    audience_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    revision: int = Field(ge=1)


class ManualTicketInput(StrictModel):
    employee_id: str
    reason: str = Field(min_length=1, max_length=200)
    message: str = Field(default="", max_length=15000)


class MaxLaunchInput(StrictModel):
    init_data: str = Field(min_length=1, max_length=16384)


class LocalLoginInput(StrictModel):
    api_key: str = Field(min_length=1, max_length=200)
