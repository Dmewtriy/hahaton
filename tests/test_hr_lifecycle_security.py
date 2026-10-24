from urllib.parse import urlparse

from conftest import BOOTSTRAP, auth, employee, scenario
from sqlalchemy import select
from test_admin_crud import employee_body

from korsik import download_links
from korsik.models import Employee, Identity, Outbox, SurveyRun, Ticket
from korsik.tickets import open_ticket
from korsik.worker import Worker


def test_hr_manages_working_roles_but_cannot_grant_trusted_access(client, runtime):
    headers = auth("hr-a-key")
    body = {"name": "Руководитель", "roles": ["manager"], "branch_codes": ["A"]}
    response = client.post("/api/identities", headers=headers, json=body)
    assert response.status_code == 201
    identity_id = response.json()["id"]
    assert (
        client.put(
            f"/api/identities/{identity_id}",
            headers=headers,
            json={
                "name": "Новый руководитель",
                "roles": ["manager"],
                "branch_codes": ["A"],
            },
        ).status_code
        == 200
    )
    for changes in [{"manage_access": True}, {"branch_codes": ["*"]}, {"branch_codes": ["B"]}]:
        assert client.post("/api/identities", headers=headers, json={**body, **changes}).status_code == 403
    assert (
        client.put(
            "/api/identities/hr-b",
            headers=headers,
            json={
                "roles": ["hr"],
                "branch_codes": ["A"],
            },
        ).status_code
        == 403
    )
    assert all(
        i["id"] not in {"bootstrap", "hr-b"} for i in client.get("/api/identities", headers=headers).json()
    )


def test_editing_employee_to_inactive_cancels_dialogue_and_restore_recovers_status(client, runtime):
    emp = employee(runtime)
    scenario(runtime)
    Worker(runtime).tick()
    with runtime.db.transaction() as session:
        open_ticket(session, emp, "Вопрос", {}, "lifecycle", human=True)
    response = client.put(
        "/api/admin/employees/e1", headers=auth("hr-a-key"), json=employee_body(active=False)
    )
    assert response.status_code == 200, response.text
    with runtime.db.transaction() as session:
        assert session.scalar(select(SurveyRun)).status == "cancelled"
        assert session.scalar(select(Ticket)).status == "closed"
        assert not session.get(Identity, emp.identity_id).active
        assert not list(
            session.scalars(
                select(Outbox).where(
                    Outbox.recipient_id == emp.identity_id,
                    Outbox.status.in_(["pending", "retry"]),
                )
            )
        )
    assert client.post("/api/admin/employees/e1/restore", headers=auth("hr-a-key")).status_code == 200
    with runtime.db.transaction() as session:
        assert session.get(Employee, "e1").status == "active"


def test_native_download_link_is_scoped_expiring_and_revocable(client, runtime, monkeypatch):
    runtime.settings.webapp_public_url = "https://example.org/app"
    doc = client.post(
        "/api/documents",
        headers=auth("hr-a-key"),
        data={"title": "Регламент", "branch_codes": '["A"]'},
        files={"file": ("rules.txt", b"test-content")},
    ).json()
    link = client.post(f"/api/documents/{doc['id']}/download-link", headers=auth("hr-a-key")).json()
    parsed = urlparse(link["url"])
    path = parsed.path + "?" + parsed.query
    assert client.get(path).content == b"test-content"
    assert client.get(path + "tampered").status_code == 401
    monkeypatch.setattr(download_links.time, "time", lambda: 99999999999)
    assert client.get(path).status_code == 401
    monkeypatch.undo()
    assert client.delete(f"/api/admin/documents/{doc['id']}", headers=auth("hr-a-key")).status_code == 200
    assert client.get(path).status_code == 403


def test_staff_unbinding_revokes_existing_web_session(client, runtime):
    login = client.post("/api/admin/auth/local", json={"api_key": "hr-a-key"}).json()
    token = login["session_token"]
    assert client.get("/api/me", headers=auth(token)).status_code == 200
    assert client.post("/api/identities/hr-a/unbind", headers=auth(BOOTSTRAP)).status_code == 200
    assert client.get("/api/me", headers=auth(token)).status_code == 401
