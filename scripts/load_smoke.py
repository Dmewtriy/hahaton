"""Small local throughput check. Uses only isolated mock data, not production services."""

import argparse
import json
import tempfile
import time
from pathlib import Path

from korsik.config import Settings
from korsik.db import Base
from korsik.models import Identity
from korsik.runtime import Runtime
from korsik.worker import Worker, ingest

parser = argparse.ArgumentParser()
parser.add_argument("--events", type=int, default=500)
args = parser.parse_args()
with tempfile.TemporaryDirectory(prefix="korsik-load-") as folder:
    root = Path(folder)
    runtime = Runtime(
        Settings(
            environment="test",
            bootstrap_token="load-test-only-000000000000000000000000",
            data_dir=root,
            database_url="sqlite:///" + str(root / "db.sqlite"),
        )
    )
    Base.metadata.create_all(runtime.db.engine)
    runtime.bootstrap()
    with runtime.db.transaction() as session:
        session.add(
            Identity(id="test-hr", name="Test", roles=["hr"], branch_codes=["A"], max_user_id="load-user")
        )
        for i in range(args.events):
            ingest(
                session,
                {
                    "event_id": f"load:{i}",
                    "kind": "message",
                    "text": "/menu",
                    "max_user_id": "load-user",
                    "payload": None,
                },
            )
    start = time.perf_counter()
    result = Worker(runtime).tick(limit=args.events + 10)
    elapsed = time.perf_counter() - start
    print(
        json.dumps(
            {
                **result,
                "seconds": round(elapsed, 3),
                "events_per_second": round(args.events / elapsed, 1),
                "transport": "mock",
                "note": "Local SQLite smoke measurement; not a MAX or ML capacity claim.",
            }
        )
    )
    runtime.close()
