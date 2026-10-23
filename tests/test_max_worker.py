from datetime import timedelta

import httpx
import pytest
from conftest import BOOTSTRAP, auth, clock, employee, event, scenario
from sqlalchemy import select

from korsik.calendar import now
from korsik.db import utcnow
from korsik.max_client import DeliveryError, MAXClient, normalize_update
from korsik.models import Identity, Job, Outbox, State
from korsik.queue import send
from korsik.worker import Worker


def update():
    return {
        "update_type": "message_created",
        "message": {
            "sender": {"user_id": 123},
            "recipient": {"chat_type": "dialog"},
            "body": {"mid": "m1", "text": "/menu"},
        },
    }


def test_normalize_max_events_and_ignore_groups():
    assert normalize_update(update())["max_user_id"] == "123"
    data = update()
    data["message"]["recipient"]["chat_type"] = "chat"
    assert normalize_update(data) is None
    callback = normalize_update(
        {
            "update_type": "message_callback",
            "callback": {"callback_id": "c1", "user": {"user_id": 123}, "payload": "hr"},
        }
    )
    assert callback["kind"] == "callback" and callback["payload"] == "hr"
    assert normalize_update({"update_type": "unknown"}) is None


def test_webhook_requires_secret_and_is_idempotent(client, runtime):
    assert client.post("/webhooks/max", json=update()).status_code == 403
    headers = {"X-Max-Bot-Api-Secret": "webhook-secret-test"}
    for _ in range(2):
        assert client.post("/webhooks/max", headers=headers, json=update()).status_code == 200
    with runtime.db.transaction() as session:
        assert len(list(session.scalars(select(Job)))) == 1


def test_max_outgoing_request_matches_contract(runtime):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"message": {"body": {"mid": "mid1"}}})

    client = MAXClient(
        runtime.settings,
        client=httpx.Client(
            base_url="https://platform-api2.max.ru",
            headers={"Authorization": "bot-secret"},
            transport=httpx.MockTransport(handler),
        ),
    )
    assert client.send("123", {"text": "Hello", "buttons": [{"text": "HR", "payload": "hr"}]}) == "mid1"
    assert requests[0].url.params["user_id"] == "123"
    assert requests[0].headers["Authorization"] == "bot-secret"
    assert b"inline_keyboard" in requests[0].content
    client.close()


@pytest.mark.parametrize(
    "status,uncertain,retryable", [(429, False, True), (500, True, False), (401, False, False)]
)
def test_max_error_policy(runtime, status, uncertain, retryable):
    client = MAXClient(
        runtime.settings,
        client=httpx.Client(
            base_url="https://max.example",
            transport=httpx.MockTransport(lambda request: httpx.Response(status, json={"code": "error"})),
        ),
    )
    with pytest.raises(DeliveryError) as error:
        client.send("1", {"text": "x"})
    assert error.value.uncertain == uncertain
    assert error.value.retryable == retryable
    client.close()


def test_unknown_delivery_is_not_automatically_resent(runtime):
    employee(runtime)

    class Uncertain:
        calls = 0

        def send(self, *args):
            self.calls += 1
            raise DeliveryError("timeout", uncertain=True)

        def close(self):
            pass

    transport = Uncertain()
    runtime.transport = transport
    event(runtime, "/menu")
    Worker(runtime).tick()
    assert transport.calls == 1
    with runtime.db.transaction() as session:
        assert session.scalar(select(Outbox)).status == "uncertain"


def test_unbound_staff_notification_does_not_block_other_messages(runtime):
    employee(runtime)
    with runtime.db.transaction() as session:
        session.add(Identity(id="unbound", name="HR", roles=["hr"], branch_codes=["A"]))
        session.flush()
        send(session, "unbound", "Waiting", "waiting", now(session))
        send(session, "i-e1", "Delivered", "delivered", now(session))
    Worker(runtime).tick()
    with runtime.db.transaction() as session:
        states = {o.dedupe_key: o.status for o in session.scalars(select(Outbox))}
        assert states == {"waiting": "pending", "delivered": "sent"}


def test_outbox_crash_recovery_marks_uncertain(runtime):
    employee(runtime)
    with runtime.db.transaction() as session:
        item = send(session, "i-e1", "Already possibly sent", "crash", now(session))
        item.status, item.lease_until = "sending", utcnow() - timedelta(seconds=10)
    Worker(runtime).tick()
    with runtime.db.transaction() as session:
        assert session.scalar(select(Outbox)).status == "uncertain"


def test_stopped_employee_receives_no_scheduled_messages(runtime):
    employee(runtime)
    event(runtime, kind="stop")
    scenario(runtime, steps=[{"id": "test", "text": "Scheduled"}])
    Worker(runtime).tick()
    assert not runtime.transport.deliveries


def test_demo_api_and_clock(client, runtime):
    response = client.put(
        "/api/demo/clock", headers=auth(BOOTSTRAP), json={"now": "2026-11-01T09:00:00+07:00"}
    )
    assert response.status_code == 200
    assert (
        client.put(
            "/api/demo/clock", headers=auth(BOOTSTRAP), json={"now": "2026-11-01T09:00:00"}
        ).status_code
        == 422
    )
    assert client.post("/api/demo/tick", headers=auth(BOOTSTRAP)).status_code == 200
    assert client.get("/dashboard").status_code == 200


def test_weekly_digest_is_generated_once(runtime):
    employee(runtime)
    with runtime.db.transaction() as session:
        session.merge(
            State(
                key="digest_policy",
                value={"enabled": True, "timezone": "Europe/Moscow", "weekday": 0, "hour": 9},
            )
        )
    clock(runtime, "2026-10-26T07:00:00+00:00")
    for _ in range(3):
        Worker(runtime).tick()
    with runtime.db.transaction() as session:
        digests = list(session.scalars(select(Outbox).where(Outbox.dedupe_key.like("digest:%"))))
        assert len(digests) == 1
        assert "Числа рассчитаны" in digests[0].body["text"]
