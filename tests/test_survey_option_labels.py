import pytest
from conftest import employee, event, scenario
from sqlalchemy import select

from korsik.models import Outbox, SurveyAnswer, SurveyRun
from korsik.worker import Worker


@pytest.mark.parametrize("kind", ["single", "multiple"])
def test_choices_show_labels_and_accept_numbers_without_exposing_ids(runtime, kind):
    employee(runtime)
    scenario(
        runtime,
        questions=[
            {
                "id": "registration",
                "text": "Были ли вопросы при оформлении?",
                "kind": kind,
                "options": [
                    {"id": "ok", "label": "Всё отлично"},
                    {"id": "questions", "label": "Остались вопросы"},
                ],
            }
        ],
    )
    Worker(runtime).tick()
    with runtime.db.transaction() as session:
        message = session.scalar(select(Outbox).where(Outbox.dedupe_key.like("survey:%")))
        assert "1. Всё отлично\n2. Остались вопросы" in message.body["text"]
        assert "ok:" not in message.body["text"] and "questions:" not in message.body["text"]
        assert [b["text"] for b in message.body["buttons"]][:2] == ["Всё отлично", "Остались вопросы"]
    event(runtime, "2")
    if kind == "multiple":
        event(runtime, "  Готово  ")
    with runtime.db.transaction() as session:
        assert session.scalar(select(SurveyRun)).status == "completed"
        assert session.scalar(select(SurveyAnswer)).value["answer"] == (
            ["questions"] if kind == "multiple" else "questions"
        )


def test_russian_label_works_and_choice_callback_keeps_original_id(runtime):
    employee(runtime)
    employee(runtime, emp_id="e2")
    scenario(
        runtime,
        questions=[
            {
                "id": "q",
                "text": "Выберите вариант",
                "kind": "single",
                "options": [{"id": "2", "label": "Первый вариант"}, {"id": "1", "label": "Второй вариант"}],
            }
        ],
    )
    Worker(runtime).tick()
    with runtime.db.transaction() as session:
        run_id = session.scalar(select(SurveyRun.id).where(SurveyRun.employee_id == "e1"))
    event(runtime, kind="callback", payload=f"a:{run_id}:0:2")
    event(runtime, "второй вариант", emp_id="e2")
    with runtime.db.transaction() as session:
        answers = {a.run_id: a.value["answer"] for a in session.scalars(select(SurveyAnswer))}
        assert answers[run_id] == "2"
        assert set(answers.values()) == {"1", "2"}
