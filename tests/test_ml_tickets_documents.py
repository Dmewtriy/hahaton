import httpx
from conftest import auth, employee, event, scenario
from sqlalchemy import select

from korsik.documents import store
from korsik.ml import HTTPML, AnswerResult, MLUnavailable, Source, StubML
from korsik.models import FeedbackAnalysis, Outbox, QuestionLog, SurveyRun, Ticket, TicketMessage
from korsik.worker import Worker


def test_document_upload_index_status_and_scope(client, runtime):
    employee(runtime)
    response = client.post(
        "/api/documents",
        headers=auth("hr-a-key"),
        data={"title": "Регламент", "branch_codes": '["A"]', "categories": "[]"},
        files={"file": ("rules.txt", b"example document", "text/plain")},
    )
    assert response.status_code == 201
    doc_id = response.json()["id"]
    Worker(runtime).tick()
    docs = client.get("/api/documents", headers=auth("key-e1")).json()
    assert docs[0]["status"] == "ready_stub"
    assert client.get(f"/api/documents/{doc_id}/download", headers=auth("key-e1")).status_code == 200
    assert client.get(f"/api/documents/{doc_id}/download", headers=auth("hr-b-key")).status_code == 403
    assert (
        client.post(
            "/api/documents",
            headers=auth("hr-a-key"),
            data={"title": "Global", "branch_codes": '["*"]'},
            files={"file": ("x.txt", b"x")},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/documents",
            headers=auth("hr-a-key"),
            data={"title": "Fake PDF", "branch_codes": '["A"]'},
            files={"file": ("x.pdf", b"not a pdf")},
        ).status_code
        == 422
    )


def test_rag_access_context_and_blocking_unapproved_sources(runtime):
    employee(runtime)

    class UntrustedML(StubML):
        def answer(self, payload):
            self.payload = payload
            return AnswerResult(
                status="answered",
                answer="LEAKED SECRET",
                sources=[Source(document_id="unauthorized", version=1)],
                processor_version="test/1",
            )

    ml = UntrustedML()
    runtime.ml = ml
    event(runtime, "/ask отпуск")
    assert ml.payload["access"] == {"branch_code": "A", "category": "office"}
    with runtime.db.transaction() as session:
        assert session.scalar(select(QuestionLog)).status == "blocked"
        assert not any("LEAKED SECRET" in o.body["text"] for o in session.scalars(select(Outbox)))


def test_rag_valid_sources_and_inaccessible_documents_are_filtered(runtime):
    employee(runtime)
    with runtime.db.transaction() as session:
        store(session, runtime.settings, "ДЕМО: портал", "portal.txt", b"demo", ["A"], [])
        store(session, runtime.settings, "Private B", "other.txt", b"private", ["B"], [])
    Worker(runtime).tick()
    event(runtime, "/ask Как открыть портал?")
    with runtime.db.transaction() as session:
        assert session.scalar(select(QuestionLog)).status == "answered"
        assert any("Источники" in o.body["text"] for o in session.scalars(select(Outbox)))


def test_ml_timeout_falls_back_and_keeps_survey(runtime):
    employee(runtime)
    scenario(runtime)
    Worker(runtime).tick()

    class Broken(StubML):
        def answer(self, payload):
            raise MLUnavailable("timeout")

    runtime.ml = Broken()
    event(runtime, "/ask Вопрос")
    with runtime.db.transaction() as session:
        assert session.scalar(select(SurveyRun)).position == 0
        assert session.scalar(select(QuestionLog)).status == "unavailable"


def test_http_ml_contract_timeout_and_auth(runtime):
    captured = []

    def handler(request):
        captured.append(request)
        if request.url.path == "/v1/analyze":
            raise httpx.ReadTimeout("timeout")
        return httpx.Response(
            200,
            json={
                "status": "not_found",
                "answer": "No source",
                "processor_version": "rag/1",
                "sources": [],
                "topics": [],
                "needs_hr": False,
            },
        )

    ml = HTTPML(
        runtime.settings,
        client=httpx.Client(
            base_url="https://ml.example",
            headers={"Authorization": "Bearer test"},
            transport=httpx.MockTransport(handler),
        ),
    )
    assert ml.answer({"request_id": "r1"}).status == "not_found"
    assert captured[0].headers["Idempotency-Key"] == "r1"
    import pytest

    with pytest.raises(MLUnavailable):
        ml.analyze({"request_id": "r2"})
    ml.close()


def test_analysis_version_and_signal_are_persisted_once(runtime):
    employee(runtime)
    scenario(runtime, questions=[{"id": "feedback", "text": "Отзыв", "kind": "text"}])
    Worker(runtime).tick()
    event(runtime, "DEMO:RED_FLAG", event_id="feedback1")
    event(runtime, "DEMO:RED_FLAG", event_id="feedback1")
    with runtime.db.transaction() as session:
        analyses = list(session.scalars(select(FeedbackAnalysis)))
        assert len(analyses) == 1 and analyses[0].processor_version == "stub/1"
        tickets = list(session.scalars(select(Ticket)))
        assert len(tickets) == 1 and tickets[0].recruiter_id == "hr-a"


def test_explicit_hr_message_keeps_survey_independent(client, runtime):
    employee(runtime)
    scenario(runtime)
    Worker(runtime).tick()
    event(runtime, "/hr Нужна помощь")
    with runtime.db.transaction() as session:
        ticket_id = session.scalar(select(Ticket.id))
    event(runtime, "/hr Вопрос для человека")
    with runtime.db.transaction() as session:
        assert not list(session.scalars(select(QuestionLog)))
        assert len(list(session.scalars(select(TicketMessage)))) == 1
        assert session.scalar(select(SurveyRun)).position == 0
    headers = {**auth("hr-a-key"), "Idempotency-Key": "reply1"}
    for _ in range(2):
        assert (
            client.post(
                f"/api/tickets/{ticket_id}/reply", headers=headers, json={"text": "Я помогу"}
            ).status_code
            == 200
        )
    with runtime.db.transaction() as session:
        assert len(list(session.scalars(select(TicketMessage)))) == 2
    assert client.post(f"/api/tickets/{ticket_id}/close", headers=auth("hr-b-key")).status_code == 403
    assert client.post(f"/api/tickets/{ticket_id}/close", headers=auth("hr-a-key")).status_code == 409
    event(runtime, "8")
    with runtime.db.transaction() as session:
        assert session.scalar(select(SurveyRun)).status == "completed"


def test_manager_dashboard_does_not_include_raw_feedback(client, runtime):
    employee(runtime)
    scenario(runtime, questions=[{"id": "feedback", "text": "Отзыв", "kind": "text"}])
    Worker(runtime).tick()
    event(runtime, "PRIVATE DEMO:RED_FLAG")
    manager = client.get("/api/dashboard", headers=auth("manager-key")).json()
    head = client.get("/api/dashboard", headers=auth("head-key")).json()
    assert "PRIVATE" not in str(manager) and "PRIVATE" not in str(head)
    assert head["totals"]["analysed_answers"] == 1
    explicit = client.get("/api/dashboard?end=2026-10-23T06:00:00Z", headers=auth("head-key")).json()
    assert explicit["totals"]["analysed_answers"] == 0  # Explicit end remains exclusive.
    from conftest import clock

    clock(runtime, "2026-10-23T06:00:01+00:00")
    head = client.get("/api/dashboard", headers=auth("head-key")).json()
    assert head["totals"]["analysed_answers"] == 1
    assert head["totals"]["stub_analyses"] == 1
    assert "risks" not in manager["totals"]
