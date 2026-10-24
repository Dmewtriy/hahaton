import pytest
from sqlalchemy import select

from korsik.models import Job, State
from korsik.worker import poll


def test_polling_saves_marker_and_uses_durable_callback_ack(runtime):
    runtime.settings.transport = "max"

    class OneBatch:
        calls = 0

        def get_updates(self, marker):
            self.calls += 1
            if self.calls > 1:
                raise KeyboardInterrupt()
            return {
                "marker": 123,
                "updates": [
                    {
                        "update_type": "message_callback",
                        "callback": {"callback_id": "cb", "user": {"user_id": 100}, "payload": "hr"},
                    }
                ],
            }

        def acknowledge(self, callback_id):
            raise AssertionError("Polling must not race the durable worker callback acknowledgement")

        def close(self):
            pass

    runtime.transport = OneBatch()
    with pytest.raises(KeyboardInterrupt):
        poll(runtime)
    with runtime.db.transaction() as session:
        assert session.get(State, "max_marker").value["marker"] == 123
        jobs = list(session.scalars(select(Job)))
        assert len(jobs) == 1 and jobs[0].payload["callback_id"] == "cb"
