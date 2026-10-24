from datetime import date, datetime
from string import Formatter
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

Role = Literal["employee", "hr", "manager", "hr_head"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @field_validator("contact_url", "adaptation_plan_url", "it_access_url", check_fields=False)
    @classmethod
    def safe_url(cls, value):
        from urllib.parse import urlsplit

        if value:
            parsed = urlsplit(value)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
            ):
                raise ValueError("Укажите HTTP(S)-ссылку без логина и пароля")
        return value or None


class IdentityCreate(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    roles: list[Role] = Field(default_factory=list)
    manage_access: bool = False
    branch_codes: list[str] = Field(default_factory=list)
    contact_url: str | None = Field(default=None, max_length=1024)


class IdentityUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    roles: list[Role]
    manage_access: bool = False
    branch_codes: list[str]
    active: bool = True
    contact_url: str | None = Field(default=None, max_length=1024)


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
    workplace: dict[str, str] = Field(default_factory=dict)
    late_join_policy: Literal["catch_up", "skip_past", "review"] = "review"
    support_recipient_ids: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("workplace")
    @classmethod
    def valid_workplace(cls, value):
        if set(value) - {"city", "street", "building", "label"} or any(len(v) > 300 for v in value.values()):
            raise ValueError("Адрес: city, street, building, label, до 300 символов каждое")
        return value

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
    auto_assign_manager: bool = True
    hire_date: date
    probation_end: date | None = None
    timezone: str | None = None
    adaptation_plan_url: str | None = Field(default=None, max_length=1024)
    it_access_url: str | None = Field(default=None, max_length=1024)

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
    reply: str | None = Field(default=None, max_length=12000)
    exclusive: bool = False
    support_actions: bool = False
    reply_after_survey: bool = False


class Question(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    text: str = Field(min_length=1, max_length=2000)
    kind: Literal["single", "multiple", "rating", "text"]
    options: list[Option] = Field(default_factory=list, max_length=20)
    minimum: int = 1
    maximum: int = 10
    comment_below: int | None = None
    comment_prompt: str = "Расскажите, пожалуйста, подробнее."
    correct_options: list[str] = Field(default_factory=list)
    points: int = Field(default=1, ge=1, le=100)
    optional_comment_on_positive: bool = False
    comment_contact_alternative: bool = False
    minimum_label: str = Field(default="", max_length=200)
    maximum_label: str = Field(default="", max_length=200)
    source_ref: str | None = None

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
        if self.correct_options and (
            self.kind not in {"single", "multiple"}
            or not set(self.correct_options) <= {o.id for o in self.options}
            or len(set(self.correct_options)) != len(self.correct_options)
            or self.kind == "single"
            and len(self.correct_options) != 1
        ):
            raise ValueError("Укажите корректные варианты правильного ответа")
        return self


class SurveyDefinition(StrictModel):
    data_mode: Literal["real", "demo"] = "real"
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    title: str = Field(min_length=1, max_length=200)
    questions: list[Question] = Field(min_length=1, max_length=100)
    reminder_days: list[StrictInt] = Field(default_factory=lambda: [1, 2], max_length=10)
    kind: Literal["survey", "quiz"] = "survey"
    pass_percent: int = Field(default=70, ge=0, le=100)
    completion_text: str = Field(default="Спасибо за обратную связь! Опрос завершён.", max_length=12000)
    source_refs: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_survey(self):
        if len({q.id for q in self.questions}) != len(self.questions):
            raise ValueError("Question IDs must be unique")
        if any(day < 1 or day > 30 for day in self.reminder_days):
            raise ValueError("Reminder offsets must be 1..30 days")
        self.reminder_days = sorted(set(self.reminder_days))
        if self.kind == "quiz" and any(not q.correct_options for q in self.questions):
            raise ValueError("Для теста у каждого вопроса должны быть правильные варианты")
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
    kind: Literal["message", "survey", "manager_reminder", "flow"] = "message"
    text: str = Field(min_length=1, max_length=15000)
    informal_text: str | None = Field(default=None, max_length=15000)
    branch_codes: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    resource_keys: list[str] = Field(default_factory=list)
    document_ids: list[str] = Field(default_factory=list)
    survey_id: str | None = None
    manual: bool = False
    reply_to: Literal["none", "hr"] = "none"
    source_refs: list[str] = Field(default_factory=list)
    actions: list[dict] = Field(default_factory=list, max_length=100)

    @field_validator("text", "informal_text")
    @classmethod
    def safe_template(cls, value):
        from .templates import TOKENS

        if value:
            for _, key, spec, conversion in Formatter().parse(value):
                if key is not None and (key not in TOKENS or spec or conversion):
                    raise ValueError("Недопустимая подстановка в тексте")
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
            if step.kind == "flow":
                if not step.actions:
                    raise ValueError("Маршрут должен содержать действия")
                ids = set()
                for node in step.actions:
                    if not node.get("id") or node["id"] in ids:
                        raise ValueError("Действия маршрута требуют уникальных ID")
                    ids.add(node["id"])
                    if node.get("kind") not in {
                        "message",
                        "material",
                        "choice",
                        "survey",
                        "employee_resource",
                        "wait",
                    }:
                        raise ValueError("Неизвестное действие маршрута")
                    if node["kind"] == "survey" and node.get("survey_id") not in survey_ids:
                        raise ValueError("Неизвестный опрос маршрута")
                    if node["kind"] == "material" and not node.get("material_key"):
                        raise ValueError("Укажите ключ материала")
                    if node["kind"] == "employee_resource" and node.get("field") not in {
                        "it_access_url",
                        "adaptation_plan_url",
                    }:
                        raise ValueError("Неизвестное поле персонального материала")
                    if node["kind"] == "wait":
                        from datetime import time

                        time.fromisoformat(node.get("at", "invalid"))
                    if node["kind"] == "choice" and not 1 <= len(node.get("options", [])) <= 20:
                        raise ValueError("Действие выбора требует от 1 до 20 вариантов")
                    StepDefinition.safe_template(node.get("text"))
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
    recommend_meeting: bool = True


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
