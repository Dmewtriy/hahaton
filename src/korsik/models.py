from datetime import date, datetime
from uuid import uuid4

from sqlalchemy import JSON, Boolean, Date, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, UTCDateTime, utcnow


def uid():
    return uuid4().hex


class Identity(Base):
    __tablename__ = "identities"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(200))
    roles: Mapped[list] = mapped_column(JSON, default=list)
    manage_access: Mapped[bool] = mapped_column(Boolean, default=False)
    branch_codes: Mapped[list] = mapped_column(JSON, default=list)
    token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    max_user_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    contact_url: Mapped[str | None] = mapped_column(String(1024))


class Scenario(Base):
    __tablename__ = "scenarios"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(200))
    version: Mapped[int] = mapped_column(Integer)
    definition: Mapped[dict] = mapped_column(JSON)
    branch_codes: Mapped[list] = mapped_column(JSON, default=lambda: ["*"])
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    kind: Mapped[str] = mapped_column(String(24), default="onboarding")
    __table_args__ = (UniqueConstraint("name", "version"),)


class Branch(Base):
    __tablename__ = "branches"
    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    timezone: Mapped[str] = mapped_column(String(100))
    workdays: Mapped[list] = mapped_column(JSON, default=lambda: [0, 1, 2, 3, 4])
    holidays: Mapped[list] = mapped_column(JSON, default=list)
    working_dates: Mapped[list] = mapped_column(JSON, default=list)
    resources: Mapped[dict] = mapped_column(JSON, default=dict)
    resource_labels: Mapped[dict] = mapped_column(JSON, default=dict)
    scenario_id: Mapped[str | None] = mapped_column(ForeignKey("scenarios.id"))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    workplace: Mapped[dict] = mapped_column(JSON, default=dict)
    late_join_policy: Mapped[str] = mapped_column(String(24), default="review")
    support_recipient_ids: Mapped[list] = mapped_column(JSON, default=list)


class Employee(Base):
    __tablename__ = "employees"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    identity_id: Mapped[str] = mapped_column(ForeignKey("identities.id"), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    branch_code: Mapped[str] = mapped_column(ForeignKey("branches.code"), index=True)
    department: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(64), default="office")
    manager_id: Mapped[str | None] = mapped_column(ForeignKey("identities.id"), index=True)
    recruiter_id: Mapped[str] = mapped_column(ForeignKey("identities.id"), index=True)
    hire_date: Mapped[date] = mapped_column(Date)
    probation_end: Mapped[date] = mapped_column(Date)
    timezone: Mapped[str] = mapped_column(String(100))
    address_style: Mapped[str] = mapped_column(String(16), default="formal")
    status: Mapped[str] = mapped_column(String(24), default="invited")
    stage: Mapped[str] = mapped_column(String(64), default="invited")
    dialogue: Mapped[dict] = mapped_column(JSON, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    schedule_revision: Mapped[int] = mapped_column(Integer, default=1)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    department_id: Mapped[str | None] = mapped_column(ForeignKey("departments.id"), index=True)
    onboarding_status: Mapped[str] = mapped_column(String(24), default="active")
    onboarding_ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    adaptation_plan_url: Mapped[str | None] = mapped_column(String(1024))
    it_access_url: Mapped[str | None] = mapped_column(String(1024))


class Invitation(Base):
    __tablename__ = "invitations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    identity_id: Mapped[str] = mapped_column(ForeignKey("identities.id"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class ScheduledStep(Base):
    __tablename__ = "scheduled_steps"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    employee_id: Mapped[str] = mapped_column(ForeignKey("employees.id"), index=True)
    step_key: Mapped[str] = mapped_column(String(100))
    scenario_id: Mapped[str] = mapped_column(ForeignKey("scenarios.id"))
    revision: Mapped[int] = mapped_column(Integer)
    due_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    payload: Mapped[dict] = mapped_column(JSON)
    campaign_id: Mapped[str | None] = mapped_column(ForeignKey("campaigns.id"), index=True)
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(300))
    __table_args__ = (UniqueConstraint("employee_id", "step_key", "revision"),)


class SurveyRun(Base):
    __tablename__ = "survey_runs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    employee_id: Mapped[str] = mapped_column(ForeignKey("employees.id"), index=True)
    scheduled_step_id: Mapped[str] = mapped_column(ForeignKey("scheduled_steps.id"), unique=True)
    definition: Mapped[dict] = mapped_column(JSON)
    position: Mapped[int] = mapped_column(Integer, default=0)
    awaiting_comment: Mapped[bool] = mapped_column(Boolean, default=False)
    selection: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(24), default="active")
    started_at: Mapped[datetime] = mapped_column(UTCDateTime)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    result: Mapped[dict | None] = mapped_column(JSON)
    optional_comment: Mapped[bool] = mapped_column(Boolean, default=False)
    branch_code_at_start: Mapped[str | None] = mapped_column(String(64))
    data_mode: Mapped[str] = mapped_column(String(16), default="real", server_default="real")


class SurveyAnswer(Base):
    __tablename__ = "survey_answers"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    run_id: Mapped[str] = mapped_column(ForeignKey("survey_runs.id"), index=True)
    question_id: Mapped[str] = mapped_column(String(100))
    value: Mapped[dict] = mapped_column(JSON)
    comment: Mapped[str | None] = mapped_column(Text)
    comment_created_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    __table_args__ = (UniqueConstraint("run_id", "question_id"),)


class FeedbackAnalysis(Base):
    __tablename__ = "feedback_analyses"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    answer_id: Mapped[str] = mapped_column(ForeignKey("survey_answers.id"), unique=True)
    employee_id: Mapped[str] = mapped_column(ForeignKey("employees.id"), index=True)
    processor_version: Mapped[str] = mapped_column(String(100))
    result: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


class Ticket(Base):
    __tablename__ = "tickets"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    employee_id: Mapped[str] = mapped_column(ForeignKey("employees.id"), index=True)
    recruiter_id: Mapped[str] = mapped_column(ForeignKey("identities.id"))
    dedupe_key: Mapped[str] = mapped_column(String(200), unique=True)
    reason: Mapped[str] = mapped_column(String(200))
    context: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(24), default="open")
    human_mode: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    resolution: Mapped[str | None] = mapped_column(Text)
    resolution_revision: Mapped[int] = mapped_column(Integer, default=0)
    confirmed_by: Mapped[str | None] = mapped_column(
        ForeignKey("identities.id", name="fk_ticket_confirmed_by")
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    outcome: Mapped[str | None] = mapped_column(String(32))


class TicketMessage(Base):
    __tablename__ = "ticket_messages"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    ticket_id: Mapped[str] = mapped_column(ForeignKey("tickets.id"), index=True)
    author_id: Mapped[str] = mapped_column(ForeignKey("identities.id"))
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


class HRMessage(Base):
    """One chronological HR inbox per employee, independent of onboarding state."""

    __tablename__ = "hr_messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    employee_id: Mapped[str] = mapped_column(ForeignKey("employees.id"), index=True)
    ticket_id: Mapped[str | None] = mapped_column(ForeignKey("tickets.id"), index=True)
    author_id: Mapped[str | None] = mapped_column(ForeignKey("identities.id"))
    kind: Mapped[str] = mapped_column(String(16))
    text: Mapped[str] = mapped_column(Text)
    dedupe_key: Mapped[str] = mapped_column(String(240), unique=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


class HRReadState(Base):
    __tablename__ = "hr_read_states"
    employee_id: Mapped[str] = mapped_column(ForeignKey("employees.id"), primary_key=True)
    reader_id: Mapped[str] = mapped_column(ForeignKey("identities.id"), primary_key=True)
    last_read_id: Mapped[int] = mapped_column(Integer, default=0)


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    title: Mapped[str] = mapped_column(String(200))
    original_name: Mapped[str] = mapped_column(String(255))
    storage_name: Mapped[str] = mapped_column(String(100))
    sha256: Mapped[str] = mapped_column(String(64))
    branch_codes: Mapped[list] = mapped_column(JSON)
    categories: Mapped[list] = mapped_column(JSON, default=list)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    external_id: Mapped[str | None] = mapped_column(String(200))
    processor_version: Mapped[str | None] = mapped_column(String(100))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    family_id: Mapped[str | None] = mapped_column(String(32), index=True)
    replaces_id: Mapped[str | None] = mapped_column(String(32))
    published: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_publish: Mapped[bool] = mapped_column(Boolean, default=True)
    last_error: Mapped[str | None] = mapped_column(String(200))
    purpose: Mapped[str] = mapped_column(String(32), default="knowledge", server_default="knowledge")
    owner_employee_id: Mapped[str | None] = mapped_column(
        ForeignKey("employees.id", name="fk_documents_owner_employee"), index=True
    )
    created_by: Mapped[str | None] = mapped_column(
        ForeignKey("identities.id", name="fk_documents_created_by")
    )


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    employee_id: Mapped[str] = mapped_column(ForeignKey("employees.id"), index=True)
    role: Mapped[str] = mapped_column(String(24))
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


class QuestionLog(Base):
    __tablename__ = "question_logs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    employee_id: Mapped[str] = mapped_column(ForeignKey("employees.id"), index=True)
    request_key: Mapped[str] = mapped_column(String(200), unique=True)
    status: Mapped[str] = mapped_column(String(32))
    topics: Mapped[list] = mapped_column(JSON, default=list)
    processor_version: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    dedupe_key: Mapped[str] = mapped_column(String(240), unique=True)
    kind: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    due_at: Mapped[datetime] = mapped_column(UTCDateTime)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_until: Mapped[datetime | None] = mapped_column(UTCDateTime)
    lease_owner: Mapped[str | None] = mapped_column(String(32))
    last_error: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    __table_args__ = (Index("ix_jobs_ready", "status", "due_at"),)


class Outbox(Base):
    __tablename__ = "outbox"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    dedupe_key: Mapped[str] = mapped_column(String(240), unique=True)
    recipient_id: Mapped[str | None] = mapped_column(ForeignKey("identities.id"), index=True)
    target_user_id: Mapped[str | None] = mapped_column(String(64))
    body: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    retry_at: Mapped[datetime] = mapped_column(UTCDateTime)
    lease_until: Mapped[datetime | None] = mapped_column(UTCDateTime)
    lease_owner: Mapped[str | None] = mapped_column(String(32))
    provider_message_id: Mapped[str | None] = mapped_column(String(200))
    last_error: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


class State(Base):
    __tablename__ = "state"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("identities.id"))
    action: Mapped[str] = mapped_column(String(100))
    resource_id: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Department(Base):
    __tablename__ = "departments"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(200))
    branch_code: Mapped[str] = mapped_column(ForeignKey("branches.code"), index=True)
    manager_id: Mapped[str | None] = mapped_column(ForeignKey("identities.id"))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = (UniqueConstraint("branch_code", "name"),)


class SurveyTemplate(Base):
    __tablename__ = "survey_templates"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    definition: Mapped[dict] = mapped_column(JSON)
    branch_codes: Mapped[list] = mapped_column(JSON)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str] = mapped_column(ForeignKey("identities.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Campaign(Base):
    __tablename__ = "campaigns"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    title: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(24))
    text: Mapped[str] = mapped_column(Text)
    document_ids: Mapped[list] = mapped_column(JSON, default=list)
    template_id: Mapped[str | None] = mapped_column(ForeignKey("survey_templates.id"))
    scenario_id: Mapped[str | None] = mapped_column(ForeignKey("scenarios.id"))
    survey_snapshot: Mapped[dict | None] = mapped_column(JSON)
    audience: Mapped[dict] = mapped_column(JSON)
    branch_codes: Mapped[list] = mapped_column(JSON)
    due_at: Mapped[datetime] = mapped_column(UTCDateTime)
    status: Mapped[str] = mapped_column(String(24), default="draft")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str] = mapped_column(ForeignKey("identities.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    source_kind: Mapped[str | None] = mapped_column(String(24))
    source_id: Mapped[str | None] = mapped_column(String(32))
    source_revision: Mapped[int | None] = mapped_column(Integer)
    message_type: Mapped[str] = mapped_column(String(24), default="message", server_default="message")


class CampaignRecipient(Base):
    __tablename__ = "campaign_recipients"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"), index=True)
    employee_id: Mapped[str] = mapped_column(ForeignKey("employees.id"), index=True)
    scheduled_step_id: Mapped[str] = mapped_column(ForeignKey("scheduled_steps.id"), unique=True)
    status: Mapped[str] = mapped_column(String(24), default="planned")
    __table_args__ = (UniqueConstraint("campaign_id", "employee_id"),)


class WebSession(Base):
    __tablename__ = "web_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    identity_id: Mapped[str] = mapped_column(ForeignKey("identities.id"), index=True)
    source: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)


class ManagerRule(Base):
    __tablename__ = "manager_rules"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    recruiter_id: Mapped[str] = mapped_column(ForeignKey("identities.id"))
    branch_code: Mapped[str] = mapped_column(ForeignKey("branches.code"))
    manager_id: Mapped[str] = mapped_column(ForeignKey("identities.id"))
    __table_args__ = (UniqueConstraint("recruiter_id", "branch_code"),)


class AssignmentHistory(Base):
    __tablename__ = "assignment_history"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    employee_id: Mapped[str] = mapped_column(ForeignKey("employees.id"), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("identities.id"))
    previous: Mapped[dict] = mapped_column(JSON)
    current: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class MeetingSignal(Base):
    __tablename__ = "meeting_signals"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    employee_id: Mapped[str] = mapped_column(ForeignKey("employees.id"), index=True)
    analysis_id: Mapped[str] = mapped_column(ForeignKey("feedback_analyses.id"), unique=True)
    manager_id: Mapped[str | None] = mapped_column(ForeignKey("identities.id"))
    status: Mapped[str] = mapped_column(String(24), default="pending")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class DigestSnapshot(Base):
    __tablename__ = "digest_snapshots"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    recipient_id: Mapped[str] = mapped_column(ForeignKey("identities.id"), index=True)
    request_key: Mapped[str] = mapped_column(String(240), unique=True)
    report: Mapped[dict] = mapped_column(JSON)
    provenance: Mapped[dict] = mapped_column(JSON)
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class RouteRun(Base):
    __tablename__ = "route_runs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    employee_id: Mapped[str] = mapped_column(ForeignKey("employees.id"), index=True)
    scheduled_step_id: Mapped[str] = mapped_column(ForeignKey("scheduled_steps.id"), unique=True)
    definition: Mapped[list] = mapped_column(JSON)
    position: Mapped[int] = mapped_column(Integer, default=0)
    answers: Mapped[dict] = mapped_column(JSON, default=dict)
    trace: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(32), default="active")
    last_error: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Material(Base):
    __tablename__ = "materials"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    source_ref: Mapped[str] = mapped_column(String(100))
    branch_codes: Mapped[list] = mapped_column(JSON)
    required: Mapped[bool] = mapped_column(Boolean, default=True)
    document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id"))
    url: Mapped[str | None] = mapped_column(String(1024))
    owner: Mapped[str] = mapped_column(String(200), default="HR")
    revision: Mapped[int] = mapped_column(Integer, default=1)


class TicketParticipant(Base):
    __tablename__ = "ticket_participants"
    ticket_id: Mapped[str] = mapped_column(ForeignKey("tickets.id"), primary_key=True)
    identity_id: Mapped[str] = mapped_column(ForeignKey("identities.id"), primary_key=True)
    kind: Mapped[str] = mapped_column(String(24))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class HelpSession(Base):
    __tablename__ = "help_sessions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    employee_id: Mapped[str] = mapped_column(ForeignKey("employees.id"), index=True)
    context: Mapped[dict] = mapped_column(JSON, default=dict)
    question: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="question")
    answer: Mapped[str | None] = mapped_column(Text)
    ticket_id: Mapped[str | None] = mapped_column(ForeignKey("tickets.id"))
    processor_version: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class NlpEvent(Base):
    __tablename__ = "nlp_events"
    event_id: Mapped[str] = mapped_column(String(150), primary_key=True)
    answer_id: Mapped[str] = mapped_column(ForeignKey("survey_answers.id"), index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    payload_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ExportFile(Base):
    __tablename__ = "export_files"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    actor_id: Mapped[str] = mapped_column(ForeignKey("identities.id"), index=True)
    filters: Mapped[dict] = mapped_column(JSON)
    employee_ids: Mapped[list] = mapped_column(JSON)
    filename: Mapped[str] = mapped_column(String(200))
    row_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)


from . import extension_models as extension_models  # noqa: E402, F401
