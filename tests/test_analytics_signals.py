from conftest import auth, employee, event, scenario
from sqlalchemy import select

from korsik.models import Ticket
from korsik.worker import Worker


def test_numeric_analytics_uses_persisted_answers(client, runtime):
    employee(runtime)
    employee(runtime, emp_id="e2")
    scenario(runtime)
    Worker(runtime).tick()
    event(runtime, "7")
    event(runtime, "Уточнить задачи")
    event(runtime, "9", emp_id="e2")
    data = client.get("/api/dashboard", headers=auth("head-key")).json()
    assert data["ratings"]["s:rating"] == {"count": 2, "mean": 8.0}
    assert data["totals"]["completed_surveys"] == 2
    manager = client.get("/api/dashboard", headers=auth("manager-key")).json()
    assert "ratings" not in manager


def test_structured_and_ml_signal_share_ticket_and_keep_explanation(runtime):
    employee(runtime)
    scenario(
        runtime,
        questions=[
            {
                "id": "problem",
                "text": "Есть проблема?",
                "kind": "single",
                "options": [{"id": "yes", "label": "Да", "needs_comment": True, "notify_hr": True}],
            }
        ],
    )
    Worker(runtime).tick()
    event(runtime, "yes")
    event(runtime, "DEMO:RED_FLAG")
    with runtime.db.transaction() as session:
        rows = list(session.scalars(select(Ticket)))
        assert len(rows) == 1
        assert rows[0].context["analysis"]["explanation"]
