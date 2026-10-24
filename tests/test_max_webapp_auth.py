import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest
from conftest import auth, employee
from sqlalchemy import select

from korsik.models import Identity, WebSession

BOT_TOKEN = "test-only-bot-token-not-used-on-network"


def launch(user_id=100, age=0, token=BOT_TOKEN):
    values = {
        "auth_date": str(int(time.time()) - age),
        "query_id": "test-query",
        "user": json.dumps({"id": user_id, "first_name": "Тест"}, ensure_ascii=False),
    }
    check = "\n".join(f"{key}={value}" for key, value in sorted(values.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    values["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(values)


def bind_hr(runtime):
    runtime.settings.max_token = BOT_TOKEN
    with runtime.db.transaction() as session:
        session.get(Identity, "hr-a").max_user_id = "100"


def test_max_launch_admin_session_and_live_role_revocation(client, runtime):
    bind_hr(runtime)
    response = client.post("/api/admin/auth/max", json={"init_data": launch()})
    assert response.status_code == 200, response.text
    token = response.json()["session_token"]
    assert client.get("/api/admin/employees", headers=auth(token)).status_code == 200
    with runtime.db.transaction() as session:
        stored = session.scalar(select(WebSession))
        assert stored.token_hash != token and stored.source == "max"
        session.get(Identity, "hr-a").roles = []
    assert client.get("/api/admin/employees", headers=auth(token)).status_code == 403


@pytest.mark.parametrize(
    "data",
    [
        lambda: launch(age=3601),
        lambda: launch(age=-120),
        lambda: launch(token="wrong"),
        lambda: launch() + "&hash=bad",
        lambda: launch() + "&auth_date=1",
    ],
)
def test_invalid_expired_or_duplicate_max_launch_is_rejected(client, runtime, data):
    bind_hr(runtime)
    assert client.post("/api/admin/auth/max", json={"init_data": data()}).status_code == 401


def test_signed_employee_still_cannot_open_admin(client, runtime):
    person = employee(runtime)
    runtime.settings.max_token = BOT_TOKEN
    with runtime.db.transaction() as session:
        session.get(Identity, person.identity_id).max_user_id = "101"
    assert client.post("/api/admin/auth/max", json={"init_data": launch(user_id=101)}).status_code == 403
    assert client.post("/api/admin/auth/local", json={"api_key": "key-e1"}).status_code == 403


def test_outer_launch_fragment_and_logout(client, runtime):
    bind_hr(runtime)
    raw = "#" + urlencode({"WebAppData": launch(), "WebAppPlatform": "web"})
    response = client.post("/api/admin/auth/max", json={"init_data": raw})
    assert response.status_code == 200
    token = response.json()["session_token"]
    assert client.post("/api/admin/auth/logout", headers=auth(token)).status_code == 200
    assert client.get("/api/admin/branches", headers=auth(token)).status_code == 401


def test_local_login_disabled_in_production(client, runtime):
    runtime.settings.environment = "production"
    assert client.post("/api/admin/auth/local", json={"api_key": "hr-a-key"}).status_code == 403
