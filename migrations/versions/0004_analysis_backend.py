"""Complete backend lifecycle, document publication, activities and reporting.

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    additions = {
        "branches": [
            sa.Column("workplace", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("late_join_policy", sa.String(24), nullable=False, server_default="catch_up"),
        ],
        "employees": [
            sa.Column("onboarding_status", sa.String(24), nullable=False, server_default="active"),
            sa.Column("onboarding_ended_at", sa.DateTime(timezone=True)),
        ],
        "survey_runs": [
            sa.Column("expires_at", sa.DateTime(timezone=True)),
            sa.Column("result", sa.JSON()),
        ],
        "documents": [
            sa.Column("family_id", sa.String(32)),
            sa.Column("replaces_id", sa.String(32)),
            sa.Column("published", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("auto_publish", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("last_error", sa.String(200)),
        ],
        "campaigns": [sa.Column("expires_at", sa.DateTime(timezone=True))],
    }
    for table, columns in additions.items():
        with op.batch_alter_table(table) as batch:
            for column in columns:
                batch.add_column(column)
    op.create_index("ix_documents_family_id", "documents", ["family_id"])
    op.execute("UPDATE documents SET family_id=id")
    op.create_table(
        "manager_rules",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("recruiter_id", sa.String(32), sa.ForeignKey("identities.id"), nullable=False),
        sa.Column("branch_code", sa.String(64), sa.ForeignKey("branches.code"), nullable=False),
        sa.Column("manager_id", sa.String(32), sa.ForeignKey("identities.id"), nullable=False),
        sa.UniqueConstraint("recruiter_id", "branch_code"),
    )
    op.create_table(
        "assignment_history",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("employee_id", sa.String(32), sa.ForeignKey("employees.id"), nullable=False),
        sa.Column("actor_id", sa.String(32), sa.ForeignKey("identities.id"), nullable=False),
        sa.Column("previous", sa.JSON(), nullable=False),
        sa.Column("current", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_assignment_history_employee_id", "assignment_history", ["employee_id"])
    op.create_table(
        "meeting_signals",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("employee_id", sa.String(32), sa.ForeignKey("employees.id"), nullable=False),
        sa.Column(
            "analysis_id", sa.String(32), sa.ForeignKey("feedback_analyses.id"), nullable=False, unique=True
        ),
        sa.Column("manager_id", sa.String(32), sa.ForeignKey("identities.id")),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_meeting_signals_employee_id", "meeting_signals", ["employee_id"])
    op.create_table(
        "digest_snapshots",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("recipient_id", sa.String(32), sa.ForeignKey("identities.id"), nullable=False),
        sa.Column("request_key", sa.String(240), nullable=False, unique=True),
        sa.Column("report", sa.JSON(), nullable=False),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_digest_snapshots_recipient_id", "digest_snapshots", ["recipient_id"])


def downgrade():
    for table in ["digest_snapshots", "meeting_signals", "assignment_history", "manager_rules"]:
        op.drop_table(table)
    op.drop_index("ix_documents_family_id", table_name="documents")
    for table, columns in {
        "campaigns": ["expires_at"],
        "documents": ["last_error", "auto_publish", "published", "replaces_id", "family_id"],
        "survey_runs": ["result", "expires_at"],
        "employees": ["onboarding_ended_at", "onboarding_status"],
        "branches": ["late_join_policy", "workplace"],
    }.items():
        with op.batch_alter_table(table) as batch:
            for column in columns:
                batch.drop_column(column)
