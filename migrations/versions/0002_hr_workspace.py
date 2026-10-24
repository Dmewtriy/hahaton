"""HR workspace: departments, reusable surveys, broadcasts and MAX sessions.

Revision ID: 0002
Revises: 0001
"""

import hashlib
import json
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "departments",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("branch_code", sa.String(64), sa.ForeignKey("branches.code"), nullable=False),
        sa.Column("manager_id", sa.String(32), sa.ForeignKey("identities.id")),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint("branch_code", "name"),
    )
    op.create_index("ix_departments_branch_code", "departments", ["branch_code"])
    op.create_table(
        "survey_templates",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column("branch_codes", sa.JSON(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(32), sa.ForeignKey("identities.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "campaigns",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("document_ids", sa.JSON(), nullable=False),
        sa.Column("template_id", sa.String(32), sa.ForeignKey("survey_templates.id")),
        sa.Column("scenario_id", sa.String(32), sa.ForeignKey("scenarios.id")),
        sa.Column("survey_snapshot", sa.JSON()),
        sa.Column("audience", sa.JSON(), nullable=False),
        sa.Column("branch_codes", sa.JSON(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(32), sa.ForeignKey("identities.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "web_sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("identity_id", sa.String(32), sa.ForeignKey("identities.id"), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_web_sessions_identity_id", "web_sessions", ["identity_id"])
    op.create_index("ix_web_sessions_expires_at", "web_sessions", ["expires_at"])
    with op.batch_alter_table("employees") as batch:
        batch.add_column(sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))
        batch.add_column(sa.Column("department_id", sa.String(32)))
        batch.create_foreign_key("fk_employees_department_id", "departments", ["department_id"], ["id"])
        batch.create_index("ix_employees_department_id", ["department_id"])
    with op.batch_alter_table("scheduled_steps") as batch:
        batch.add_column(sa.Column("campaign_id", sa.String(32)))
        batch.create_foreign_key("fk_steps_campaign_id", "campaigns", ["campaign_id"], ["id"])
        batch.create_index("ix_scheduled_steps_campaign_id", ["campaign_id"])
    op.create_table(
        "campaign_recipients",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("campaign_id", sa.String(32), sa.ForeignKey("campaigns.id"), nullable=False),
        sa.Column("employee_id", sa.String(32), sa.ForeignKey("employees.id"), nullable=False),
        sa.Column(
            "scheduled_step_id",
            sa.String(32),
            sa.ForeignKey("scheduled_steps.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("status", sa.String(24), nullable=False),
        sa.UniqueConstraint("campaign_id", "employee_id"),
    )
    op.create_index("ix_campaign_recipients_campaign_id", "campaign_recipients", ["campaign_id"])
    op.create_index("ix_campaign_recipients_employee_id", "campaign_recipients", ["employee_id"])
    with op.batch_alter_table("branches") as batch:
        batch.add_column(sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))
        batch.add_column(sa.Column("resource_labels", sa.JSON(), nullable=False, server_default="{}"))
    with op.batch_alter_table("documents") as batch:
        batch.add_column(sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))
    with op.batch_alter_table("scenarios") as batch:
        batch.add_column(sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()))
        batch.add_column(sa.Column("kind", sa.String(24), nullable=False, server_default="onboarding"))
    connection = op.get_bind()
    for row in connection.execute(
        sa.text("SELECT DISTINCT branch_code, department FROM employees")
    ).mappings():
        department_id = hashlib.sha256((row["branch_code"] + "\0" + row["department"]).encode()).hexdigest()[
            :32
        ]
        connection.execute(
            sa.text(
                "INSERT INTO departments (id,name,branch_code,active,revision) "
                "VALUES (:id,:name,:branch,:active,1)"
            ),
            {"id": department_id, "name": row["department"], "branch": row["branch_code"], "active": True},
        )
        connection.execute(
            sa.text("UPDATE employees SET department_id=:id WHERE branch_code=:branch AND department=:name"),
            {"id": department_id, "branch": row["branch_code"], "name": row["department"]},
        )

    creator = connection.execute(sa.text("SELECT id FROM identities WHERE id='bootstrap'")).scalar()
    if not creator:
        creator = connection.execute(sa.text("SELECT id FROM identities LIMIT 1")).scalar()
    if creator:
        templates = {}
        for row in connection.execute(
            sa.text("SELECT name, definition, branch_codes FROM scenarios ORDER BY version")
        ).mappings():
            definition = (
                json.loads(row["definition"]) if isinstance(row["definition"], str) else row["definition"]
            )
            for survey in definition.get("surveys", []):
                template_id = hashlib.sha256((row["name"] + ":" + survey["id"]).encode()).hexdigest()[:32]
                templates[template_id] = {
                    "id": template_id,
                    "title": survey["title"],
                    "description": "Опрос адаптации",
                    "definition": {**survey, "id": template_id},
                    "branch_codes": json.loads(row["branch_codes"])
                    if isinstance(row["branch_codes"], str)
                    else row["branch_codes"],
                    "active": True,
                    "revision": 1,
                    "created_by": creator,
                    "created_at": datetime.now(UTC),
                    "updated_at": datetime.now(UTC),
                }
        if templates:
            table = sa.table(
                "survey_templates",
                sa.column("id", sa.String),
                sa.column("title", sa.String),
                sa.column("description", sa.Text),
                sa.column("definition", sa.JSON),
                sa.column("branch_codes", sa.JSON),
                sa.column("active", sa.Boolean),
                sa.column("revision", sa.Integer),
                sa.column("created_by", sa.String),
                sa.column("created_at", sa.DateTime(timezone=True)),
                sa.column("updated_at", sa.DateTime(timezone=True)),
            )
            op.bulk_insert(table, list(templates.values()))


def downgrade():
    op.drop_table("campaign_recipients")
    with op.batch_alter_table("scheduled_steps") as batch:
        batch.drop_index("ix_scheduled_steps_campaign_id")
        batch.drop_constraint("fk_steps_campaign_id", type_="foreignkey")
        batch.drop_column("campaign_id")
    with op.batch_alter_table("employees") as batch:
        batch.drop_index("ix_employees_department_id")
        batch.drop_constraint("fk_employees_department_id", type_="foreignkey")
        batch.drop_column("department_id")
        batch.drop_column("revision")
    for table, columns in [
        ("branches", ["revision", "resource_labels"]),
        ("documents", ["revision"]),
        ("scenarios", ["active", "kind"]),
    ]:
        with op.batch_alter_table(table) as batch:
            for column in columns:
                batch.drop_column(column)
    op.drop_table("web_sessions")
    op.drop_table("campaigns")
    op.drop_table("survey_templates")
    op.drop_table("departments")
