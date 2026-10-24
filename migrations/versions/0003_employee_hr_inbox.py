"""Independent HR inbox, read cursors and explicit HR replies in onboarding.

Revision ID: 0003
Revises: 0002
"""

import json
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def decoded(value):
    return json.loads(value) if isinstance(value, str) else value


def instant(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def upgrade():
    op.create_table(
        "hr_messages",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("employee_id", sa.String(32), sa.ForeignKey("employees.id"), nullable=False),
        sa.Column("ticket_id", sa.String(32), sa.ForeignKey("tickets.id")),
        sa.Column("author_id", sa.String(32), sa.ForeignKey("identities.id")),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("dedupe_key", sa.String(240), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_hr_messages_employee_id", "hr_messages", ["employee_id"])
    op.create_index("ix_hr_messages_ticket_id", "hr_messages", ["ticket_id"])
    op.create_table(
        "hr_read_states",
        sa.Column("employee_id", sa.String(32), sa.ForeignKey("employees.id"), primary_key=True),
        sa.Column("reader_id", sa.String(32), sa.ForeignKey("identities.id"), primary_key=True),
        sa.Column("last_read_id", sa.Integer(), nullable=False),
    )
    connection = op.get_bind()
    people = {
        row["id"]: row
        for row in connection.execute(
            sa.text("SELECT id, identity_id, recruiter_id FROM employees")
        ).mappings()
    }
    events = []
    for ticket in connection.execute(sa.text("SELECT * FROM tickets")).mappings():
        person = people[ticket["employee_id"]]
        context = decoded(ticket["context"])
        if context.get("answer_id"):
            answer = (
                connection.execute(
                    sa.text(
                        "SELECT a.value, a.comment, a.question_id, r.definition FROM survey_answers a "
                        "JOIN survey_runs r ON r.id=a.run_id WHERE a.id=:answer"
                    ),
                    {"answer": context["answer_id"]},
                )
                .mappings()
                .first()
            )
            if answer:
                definition = decoded(answer["definition"])
                question = next((q for q in definition["questions"] if q["id"] == answer["question_id"]), {})
                options = {o["id"]: o["label"] for o in question.get("options", [])}
                value = decoded(answer["value"])["answer"]
                context["question"] = question.get("text", "Ответ на опрос")
                context["message"] = ", ".join(
                    options.get(v, str(v)) for v in (value if isinstance(value, list) else [value])
                )
                if answer["comment"]:
                    context["message"] += "\nКомментарий: " + answer["comment"]
        kind = (
            "system"
            if context.get("initiated_by_hr")
            else "signal"
            if context.get("answer_id") or context.get("analysis")
            else "employee"
        )
        text = "\n".join(
            str(v)
            for v in [
                ticket["reason"],
                context.get("question"),
                context.get("message"),
                context.get("analysis", {}).get("explanation"),
            ]
            if v
        )
        events.append(
            {
                "employee_id": person["id"],
                "ticket_id": ticket["id"],
                "kind": kind,
                "author_id": context.get("initiated_by_hr")
                if kind == "system"
                else person["identity_id"]
                if kind == "employee"
                else None,
                "text": text,
                "dedupe_key": "ticket-open:" + ticket["id"],
                "created_at": instant(ticket["created_at"]),
            }
        )
        for message in connection.execute(
            sa.text("SELECT * FROM ticket_messages WHERE ticket_id=:ticket"), {"ticket": ticket["id"]}
        ).mappings():
            events.append(
                {
                    "employee_id": person["id"],
                    "ticket_id": ticket["id"],
                    "author_id": message["author_id"],
                    "kind": "employee" if message["author_id"] == person["identity_id"] else "hr",
                    "text": message["text"],
                    "dedupe_key": "legacy-message:" + message["id"],
                    "created_at": instant(message["created_at"]),
                }
            )
    messages = sa.Table("hr_messages", sa.MetaData(), autoload_with=connection)
    reads = sa.Table("hr_read_states", sa.MetaData(), autoload_with=connection)
    last_replies = {}
    for event in sorted(
        events,
        key=lambda e: (
            e["created_at"],
            e["ticket_id"],
            e["dedupe_key"].startswith("legacy-message:"),
            e["dedupe_key"],
        ),
    ):
        message_id = connection.execute(
            messages.insert().values(**event).returning(messages.c.id)
        ).scalar_one()
        if event["kind"] == "hr":
            last_replies[event["employee_id"]] = message_id
    for employee_id, last_id in last_replies.items():
        connection.execute(
            reads.insert().values(
                employee_id=employee_id, reader_id=people[employee_id]["recruiter_id"], last_read_id=last_id
            )
        )
    connection.execute(sa.text("UPDATE tickets SET human_mode=:off"), {"off": False})
    fix_intro(connection, upgrading=True)


def fix_intro(connection, upgrading):
    for table, key, field in [("scenarios", "id", "definition"), ("scheduled_steps", "id", "payload")]:
        reflected = sa.Table(table, sa.MetaData(), autoload_with=connection)
        for row in connection.execute(sa.select(reflected)).mappings():
            data = decoded(row[field])
            steps = data.get("steps", []) if table == "scenarios" else [data]
            changed = False
            for step in steps:
                if upgrading:
                    if (
                        step.get("id") == "introduction" or row.get("step_key") == "introduction"
                    ) and "Расскажите рекрутеру" in step.get("text", ""):
                        step["reply_to"] = "hr"
                        changed = True
                elif "reply_to" in step:
                    step.pop("reply_to")
                    changed = True
            if changed:
                connection.execute(
                    reflected.update().where(reflected.c[key] == row[key]).values({field: data})
                )
    employees = sa.Table("employees", sa.MetaData(), autoload_with=connection)
    for employee in connection.execute(sa.select(employees)).mappings():
        dialogue = decoded(employee["dialogue"])
        if upgrading and employee["stage"] == "introduction" and not dialogue.get("awaiting_ai_question"):
            step_id = connection.execute(
                sa.text(
                    "SELECT id FROM scheduled_steps WHERE employee_id=:e AND step_key='introduction' AND status='dispatched' ORDER BY revision DESC LIMIT 1"
                ),
                {"e": employee["id"]},
            ).scalar()
            if step_id:
                dialogue["awaiting_hr_message"] = {"step_id": step_id, "survey_run_id": None}
        if not upgrading:
            dialogue.pop("awaiting_hr_message", None)
        connection.execute(
            employees.update().where(employees.c.id == employee["id"]).values(dialogue=dialogue)
        )


def downgrade():
    fix_intro(op.get_bind(), upgrading=False)
    op.drop_table("hr_read_states")
    op.drop_table("hr_messages")
