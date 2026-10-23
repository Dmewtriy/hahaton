"""Validate PostgreSQL migrations and locking in an isolated, dedicated schema."""

import os
import subprocess
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from korsik.config import Settings

settings = Settings()
if settings.environment == "production" or not settings.database_url.startswith("postgresql"):
    raise SystemExit("Run only against a development PostgreSQL instance")
engine = create_engine(settings.database_url)
with engine.begin() as connection:
    connection.execute(text("CREATE SCHEMA IF NOT EXISTS korsik_validation"))
engine.dispose()
url = make_url(settings.database_url).update_query_dict({"options": "-csearch_path=korsik_validation"})
environment = {
    **os.environ,
    "KORSIK_DATABASE_URL": url.render_as_string(hide_password=False),
    "KORSIK_TEST_DATABASE_URL": url.render_as_string(hide_password=False),
}
for args in [
    ["-m", "korsik.cli", "migrate"],
    ["-m", "alembic", "check"],
    ["-m", "pytest", "-q", "tests/test_postgres.py"],
]:
    subprocess.run([sys.executable, *args], env=environment, check=True)
