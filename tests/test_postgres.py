"""Optional integration against a dedicated test database; never a production database."""

import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import select

from korsik.calendar import now
from korsik.config import Settings
from korsik.models import Job
from korsik.queue import enqueue, send
from korsik.runtime import Runtime
from korsik.worker import Worker


@pytest.mark.skipif(
    not os.environ.get("KORSIK_TEST_DATABASE_URL"), reason="Dedicated PostgreSQL test DB not set"
)
def test_postgres_job_claiming_and_outbox(tmp_path):
    runtime = Runtime(
        Settings(
            database_url=os.environ["KORSIK_TEST_DATABASE_URL"],
            bootstrap_token="postgres-test-only-key-0000000000000000",
            data_dir=tmp_path,
            environment="test",
            transport="mock",
            ml_mode="stub",
            max_ca_file=None,
            enable_demo=False,
        )
    )
    runtime.bootstrap()
    prefix = "pg-test:" + uuid4().hex
    with runtime.db.transaction() as session:
        # Isolate this queue slice from any demo scheduling data.
        enqueue(session, "max_ack", {"callback_id": "test"}, prefix + ":job", now(session))
        send(session, "bootstrap", "Notification awaiting MAX binding", prefix + ":outbox", now(session))
    workers = [Worker(runtime), Worker(runtime)]
    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(lambda worker: worker.process_one(), workers))
    Worker(runtime).deliver_one()  # Exercises the PostgreSQL outer-join/row-lock query.
    with runtime.db.transaction() as session:
        job = session.scalar(select(Job).where(Job.dedupe_key == prefix + ":job"))
        assert job.status == "done" and job.attempts == 1
    runtime.close()
