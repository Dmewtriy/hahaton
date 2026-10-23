from datetime import timedelta

from conftest import BOOTSTRAP, auth, employee
from sqlalchemy import select

from korsik.calendar import now
from korsik.employees import bind, invite
from korsik.models import Identity, Invitation
from korsik.tickets import open_ticket


def test_access_management_is_separate_from_business_roles(client, runtime):
    employee(runtime)
    assert client.get("/api/employees", headers=auth(BOOTSTRAP)).json() == []
    assert client.get("/api/identities", headers=auth("hr-a-key")).status_code == 403
    response = client.post(
        "/api/identities",
        headers=auth(BOOTSTRAP),
        json={"name": "Access admin", "roles": [], "manage_access": True, "branch_codes": []},
    )
    assert response.status_code == 201
    assert "api_token" in response.json()


def test_row_level_scope_and_manager_privacy(client, runtime):
    a = employee(runtime)
    b = employee(runtime, emp_id="e2", branch="B")
    assert client.get(f"/api/employees/{a.id}", headers=auth("manager-key")).status_code == 200
    assert client.get(f"/api/employees/{b.id}", headers=auth("manager-key")).status_code == 403
    assert client.get(f"/api/employees/{a.id}/analyses", headers=auth("head-key")).status_code == 403
    dashboard = client.get("/api/dashboard", headers=auth("manager-key")).json()
    assert dashboard["totals"]["employees"] == 1
    assert "sentiments" not in dashboard["totals"]
    assert client.get(f"/api/employees/{b.id}", headers=auth("hr-a-key")).status_code == 403


def test_unassigned_hr_cannot_read_employee_in_same_branch(client, runtime):
    employee(runtime)
    with runtime.db.transaction() as session:
        from korsik.access import token_hash

        session.add(
            Identity(
                id="other-hr",
                name="Other HR",
                roles=["hr"],
                branch_codes=["A"],
                token_hash=token_hash("other-key"),
            )
        )
    assert client.get("/api/employees/e1", headers=auth("other-key")).status_code == 403


def test_invitation_single_use_expiry_and_unique_binding(runtime):
    emp = employee(runtime, bound=False)
    with runtime.db.transaction() as session:
        token = invite(session, emp.identity_id, 1)
        assert bind(session, token, "new-user").id == emp.identity_id
        assert bind(session, token, "attacker") is None
        assert session.scalar(select(Invitation)).token_hash != token
    other = employee(runtime, emp_id="e2", bound=False)
    with runtime.db.transaction() as session:
        token = invite(session, other.identity_id, 1)
        assert bind(session, token, "new-user") is None
        invitation = session.scalar(select(Invitation).where(Invitation.identity_id == other.identity_id))
        invitation.expires_at = now(session) - timedelta(seconds=1)
        assert bind(session, token, "other-user") is None


def test_blocked_api_key_is_revoked_immediately(client, runtime):
    employee(runtime)
    with runtime.db.transaction() as session:
        session.get(Identity, "hr-a").active = False
    assert client.get("/api/me", headers=auth("hr-a-key")).status_code == 401


def test_head_cannot_read_private_ticket_context(client, runtime):
    emp = employee(runtime)
    with runtime.db.transaction() as session:
        ticket = open_ticket(session, emp, "private", {"text": "private feedback"}, "t1")
        ticket_id = ticket.id
    assert client.get(f"/api/tickets/{ticket_id}", headers=auth("head-key")).status_code == 403
    assert client.get(f"/api/tickets/{ticket_id}", headers=auth("hr-a-key")).status_code == 200
    assert client.get(f"/api/tickets/{ticket_id}", headers=auth("key-e1")).status_code == 200


def test_hr_cannot_assign_other_recruiter(client, runtime):
    response = client.post(
        "/api/employees",
        headers=auth("hr-a-key"),
        json={
            "name": "A",
            "branch_code": "A",
            "department": "D",
            "hire_date": "2026-10-26",
            "recruiter_id": "hr-b",
        },
    )
    assert response.status_code == 403


def test_no_authentication_no_access(client):
    assert client.get("/api/employees").status_code == 401
    assert client.get("/api/me", headers=auth("wrong")).status_code == 401


def test_staff_directory_respects_branch_scope(client, runtime):
    employee(runtime)
    entries = client.get("/api/staff-directory", headers=auth("hr-a-key")).json()
    assert {entry["id"] for entry in entries} == {"hr-a", "manager", "head"}
    assert client.get("/api/staff-directory", headers=auth("key-e1")).status_code == 403
