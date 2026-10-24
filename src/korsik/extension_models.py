"""Persisted v0.5/v0.6 objects; employee support remains a separate domain."""

from datetime import datetime

from sqlalchemy import JSON, Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, UTCDateTime, utcnow
from .models import uid


class PlannedEvent(Base):
    __tablename__ = "planned_events"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    kind: Mapped[str] = mapped_column(String(24))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    branch_code: Mapped[str] = mapped_column(ForeignKey("branches.code"), index=True)
    starts_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    timezone: Mapped[str] = mapped_column(String(100))
    location: Mapped[str] = mapped_column(String(1024), default="")
    organizer_id: Mapped[str] = mapped_column(ForeignKey("identities.id"))
    audience: Mapped[dict] = mapped_column(JSON)
    document_ids: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(24), default="draft")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class EntityRevision(Base):
    __tablename__ = "entity_revisions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    entity_id: Mapped[str] = mapped_column(String(32), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    actor_id: Mapped[str] = mapped_column(ForeignKey("identities.id"))
    snapshot: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    __table_args__ = (UniqueConstraint("entity_id", "revision"),)


class AssignmentGrant(Base):
    __tablename__ = "assignment_grants"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    issuer_id: Mapped[str] = mapped_column(ForeignKey("identities.id"))
    assignee_id: Mapped[str] = mapped_column(ForeignKey("identities.id"))
    branch_code: Mapped[str] = mapped_column(ForeignKey("branches.code"))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("identities.id"))
    __table_args__ = (UniqueConstraint("issuer_id", "assignee_id", "branch_code"),)


class ManagementConversation(Base):
    __tablename__ = "management_conversations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    issuer_id: Mapped[str] = mapped_column(ForeignKey("identities.id"))
    assignee_id: Mapped[str] = mapped_column(ForeignKey("identities.id"))
    last_seq: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    __table_args__ = (UniqueConstraint("issuer_id", "assignee_id"),)


class ManagementTask(Base):
    __tablename__ = "management_tasks"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    display_number: Mapped[str] = mapped_column(String(32), unique=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("management_conversations.id"), index=True)
    branch_code: Mapped[str] = mapped_column(ForeignKey("branches.code"))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    expected_result: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(24), default="assigned")
    due_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    source_insight_id: Mapped[str | None] = mapped_column(ForeignKey("improvement_insights.id"))
    source_report_id: Mapped[str | None] = mapped_column(ForeignKey("improvement_reports.id"))
    source_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    replaces_task_id: Mapped[str | None] = mapped_column(ForeignKey("management_tasks.id"))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    result: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    mode: Mapped[str] = mapped_column(String(16), default="real", server_default="real")
    __table_args__ = (
        Index(
            "uq_management_active_insight",
            "source_insight_id",
            unique=True,
            sqlite_where=text(
                "source_insight_id IS NOT NULL AND status IN ('assigned','in_progress','blocked')"
            ),
            postgresql_where=text(
                "source_insight_id IS NOT NULL AND status IN ('assigned','in_progress','blocked')"
            ),
        ),
    )


class ManagementEntry(Base):
    __tablename__ = "management_entries"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("management_conversations.id"), index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("management_tasks.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    actor_id: Mapped[str] = mapped_column(ForeignKey("identities.id"))
    kind: Mapped[str] = mapped_column(String(32))
    body: Mapped[dict] = mapped_column(JSON)
    request_key: Mapped[str] = mapped_column(String(160), unique=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    __table_args__ = (UniqueConstraint("conversation_id", "seq"),)


class ManagementReadState(Base):
    __tablename__ = "management_read_states"
    conversation_id: Mapped[str] = mapped_column(ForeignKey("management_conversations.id"), primary_key=True)
    identity_id: Mapped[str] = mapped_column(ForeignKey("identities.id"), primary_key=True)
    last_seq: Mapped[int] = mapped_column(Integer, default=0)


class ImprovementObservation(Base):
    __tablename__ = "improvement_observations"
    source_key: Mapped[str] = mapped_column(String(160), primary_key=True)
    employee_id: Mapped[str] = mapped_column(ForeignKey("employees.id"), index=True)
    branch_code: Mapped[str] = mapped_column(ForeignKey("branches.code"), index=True)
    category: Mapped[str] = mapped_column(String(32))
    topic: Mapped[str] = mapped_column(String(300))
    topics: Mapped[list] = mapped_column(JSON, default=list)
    source_revision: Mapped[int] = mapped_column(Integer, default=1)
    mode: Mapped[str] = mapped_column(String(16))
    terminal_status: Mapped[str] = mapped_column(String(32))
    helpful: Mapped[bool | None] = mapped_column(Boolean)
    question_key: Mapped[str | None] = mapped_column(String(300))
    comment_present: Mapped[bool] = mapped_column(Boolean, default=False)
    mapping_version: Mapped[str] = mapped_column(String(32), default="topics/1")
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime)


class ImprovementInsight(Base):
    __tablename__ = "improvement_insights"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    branch_code: Mapped[str] = mapped_column(ForeignKey("branches.code"))
    category: Mapped[str] = mapped_column(String(32))
    topic: Mapped[str] = mapped_column(String(300))
    mode: Mapped[str] = mapped_column(String(16))
    snoozed_until: Mapped[datetime | None] = mapped_column(UTCDateTime)
    __table_args__ = (UniqueConstraint("branch_code", "category", "topic", "mode"),)


class ImprovementReport(Base):
    __tablename__ = "improvement_reports"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    actor_id: Mapped[str] = mapped_column(ForeignKey("identities.id"))
    mode: Mapped[str] = mapped_column(String(16))
    snapshot: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
