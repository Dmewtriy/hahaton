from datetime import date, datetime
from string import Formatter
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Role = Literal["employee", "hr", "manager", "hr_head"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class IdentityCreate(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    roles: list[Role] = Field(default_factory=list)
    manage_access: bool = False
    branch_codes: list[str] = Field(default_factory=list)


class IdentityUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    roles: list[Role]
    manage_access: bool = False
    branch_codes: list[str]
    active: bool = True


class BranchInput(StrictModel):
    code: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    name: str = Field(min_length=1, max_length=200)
    timezone: str = "Europe/Moscow"
    workdays: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4])
    holidays: list[date] = Field(default_factory=list)
    working_dates: list[date] = Field(default_factory=list)
    resources: dict[str, str] = Field(default_factory=dict)
    scenario_id: str | None = None
    active: bool = True

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        try:
            ZoneInfo(value)
        except Exception as exc:
            raise ValueError("Unknown IANA timezone") from exc
        return value

    @field_validator("workdays")
    @classmethod
    def valid_workdays(cls, value):
        if not value or len(set(value)) != len(value) or any(d < 0 or d > 6 for d in value):
            raise ValueError("workdays must contain unique weekday numbers 0..6")
        return value

    @field_validator("resources")
    @classmethod
    def valid_resources(cls, value):
        if any(not k.isidentifier() or len(v) > 4000 for k, v in value.items()):
            raise ValueError("Use simple resource keys and values of at most 4000 characters")
        return value


class EmployeeCreate(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    branch_code: str
    department: str = Field(min_length=1, max_length=200)
    category: str = Field(default="office", min_length=1, max_length=64)
    recruiter_id: str
    manager_id: str | None = None
    hire_date: date
    probation_end: date | None = None
    timezone: str | None = None

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        if value:
            try:
                ZoneInfo(value)
            except Exception as exc:
                raise ValueError("Unknown IANA timezone") from exc
        return value


class EmployeeDates(StrictModel):
    hire_date: date
    probation_end: date | None = None


class Option(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,40}$")
    label: str = Field(min_length=1, max_length=150)
    needs_comment: bool = False
    notify_hr: bool = False
    reply: str | None = Field(default=None, max_length=2000)


class Question(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    text: str = Field(min_length=1, max_length=2000)
    kind: Literal["single", "multiple", "rating", "text"]
    options: list[Option] = Field(default_factory=list, max_length=20)
    minimum: int = 1
    maximum: int = 10
    comment_below: int | None = None
    comment_prompt: str = "Расскажите, пожалуйста, подробнее."

    @model_validator(mode="after")
    def validate_question(self):
        if self.kind in {"single", "multiple"} and not self.options:
            raise ValueError("Choice questions require options")
        if len({o.id for o in self.options}) != len(self.options):
            raise ValueError("Option IDs must be unique")
        if self.kind == "multiple" and any(o.id == "done" for o in self.options):
            raise ValueError("Option ID 'done' is reserved for completing a multiple choice")
        if not 0 <= self.minimum < self.maximum <= 100:
            raise ValueError("Rating bounds must satisfy 0 <= minimum < maximum <= 100")
        if self.comment_below is not None and not self.minimum <= self.comment_below <= self.maximum:
            raise ValueError("comment_below must be inside the rating range")
        return self


class SurveyDefinition(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    title: str = Field(min_length=1, max_length=200)
    questions: list[Question] = Field(min_length=1, max_length=100)
    reminder_days: list[int] = Field(default_factory=lambda: [1, 2], max_length=10)

    @model_validator(mode="after")
    def validate_survey(self):
        if len({q.id for q in self.questions}) != len(self.questions):
            raise ValueError("Question IDs must be unique")
        if any(day < 1 or day > 30 for day in self.reminder_days):
            raise ValueError("Reminder offsets must be 1..30 days")
        return self


class Schedule(StrictModel):
    anchor: Literal["hire", "probation_end"] = "hire"
    months: int = Field(default=0, ge=-12, le=24)
    days: int = Field(default=0, ge=-365, le=730)
    working_day: int | None = Field(default=None, ge=1, le=365)
    at: str = Field(default="09:00", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    next_workday: bool = False


class StepDefinition(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    schedule: Schedule = Field(default_factory=Schedule)
    kind: Literal["message", "survey", "manager_reminder"] = "message"
    text: str = Field(min_length=1, max_length=15000)
    informal_text: str | None = Field(default=None, max_length=15000)
    branch_codes: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    resource_keys: list[str] = Field(default_factory=list)
    document_ids: list[str] = Field(default_factory=list)
    survey_id: str | None = None
    manual: bool = False
    reply_to: Literal["none", "hr"] = "none"

    @field_validator("text", "informal_text")
    @classmethod
    def safe_template(cls, value):
        if value:
            for _, key, spec, conversion in Formatter().parse(value):
                if key is not None and (key not in {"name", "branch", "department"} or spec or conversion):
                    raise ValueError("Only {name}, {branch}, {department} placeholders are supported")
        return value


class ScenarioDefinition(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    version: int = Field(ge=1)
    steps: list[StepDefinition] = Field(min_length=1, max_length=500)
    surveys: list[SurveyDefinition] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_references(self):
        if len({s.id for s in self.steps}) != len(self.steps):
            raise ValueError("Step IDs must be unique")
        survey_ids = {s.id for s in self.surveys}
        if len(survey_ids) != len(self.surveys):
            raise ValueError("Survey IDs must be unique")
        for step in self.steps:
            if step.kind == "survey" and step.survey_id not in survey_ids:
                raise ValueError(f"Unknown survey in step {step.id}")
        return self


class DemoEvent(StrictModel):
    event_id: str = Field(min_length=1, max_length=150)
    max_user_id: str = Field(min_length=1, max_length=64)
    kind: Literal["message", "start", "callback", "stop"] = "message"
    text: str = Field(default="", max_length=15000)
    payload: str | None = Field(default=None, max_length=500)


class ClockInput(StrictModel):
    now: datetime | None

    @field_validator("now")
    @classmethod
    def aware(cls, value):
        if value is not None and value.tzinfo is None:
            raise ValueError("Include timezone offset")
        return value


class TextInput(StrictModel):
    text: str = Field(min_length=1, max_length=15000)


class DeliveryResolution(StrictModel):
    action: Literal["mark_sent", "retry"]
    accept_possible_duplicate: bool = False


class EscalationRules(StrictModel):
    enabled: bool = True
    minimum_risk: Literal["low", "medium", "high"] = "high"
    topics: list[str] = Field(default_factory=list)


class DigestPolicy(StrictModel):
    enabled: bool = True
    timezone: str = "Europe/Moscow"
    weekday: int = Field(default=0, ge=0, le=6)
    hour: int = Field(default=9, ge=0, le=23)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        try:
            ZoneInfo(value)
        except Exception as exc:
            raise ValueError("Unknown IANA timezone") from exc
        return value
