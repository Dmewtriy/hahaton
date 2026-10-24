from conftest import BOOTSTRAP, auth, employee

from korsik.models import Identity


def employee_body(**changes):
    return {
        "name": "Новый сотрудник",
        "branch_code": "A",
        "department": "Отдел разработки",
        "category": "office",
        "recruiter_id": "hr-a",
        "manager_id": "manager",
        "hire_date": "2026-10-26",
        "timezone": "Europe/Moscow",
        **changes,
    }


def test_admin_employee_crud_invitation_and_revision(client, runtime):
    headers = auth("hr-a-key")
    created = client.post("/api/admin/employees", headers=headers, json=employee_body())
    assert created.status_code == 201, created.text
    entry = created.json()
    assert entry["department_id"] and entry["revision"] == 1
    assert client.get(f"/api/admin/employees/{entry['id']}", headers=headers).status_code == 200
    changed = client.put(
        f"/api/admin/employees/{entry['id']}",
        headers=headers,
        json=employee_body(name="Изменённое имя", revision=entry["revision"]),
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["name"] == "Изменённое имя"
    assert (
        client.put(
            f"/api/admin/employees/{entry['id']}",
            headers=headers,
            json=employee_body(revision=entry["revision"]),
        ).status_code
        == 409
    )
    invitation = client.post(f"/api/admin/employees/{entry['id']}/invitation", headers=headers)
    assert invitation.status_code == 200 and invitation.json()["command"].startswith("/start ")
    assert client.delete(f"/api/admin/employees/{entry['id']}", headers=headers).status_code == 200
    assert client.get("/api/admin/employees", headers=headers).json() == []
    assert len(client.get("/api/admin/employees?archived=true", headers=headers).json()) == 1
    assert client.post(f"/api/admin/employees/{entry['id']}/restore", headers=headers).status_code == 200
    with runtime.db.transaction() as session:
        assert session.get(Identity, entry["identity_id"]).active


def test_admin_api_rejects_non_admin_and_outside_scope(client, runtime):
    employee(runtime)
    employee(runtime, emp_id="b", branch="B")
    for path in ["employees", "branches", "departments", "documents", "surveys", "campaigns", "tickets"]:
        assert client.get("/api/admin/" + path, headers=auth("key-e1")).status_code == 403
        assert client.get("/api/admin/" + path, headers=auth("manager-key")).status_code == 403
        assert client.get("/api/admin/" + path).status_code == 401
    assert client.get("/api/admin/employees/b", headers=auth("hr-a-key")).status_code == 403
    assert client.get("/api/admin/employees", headers=auth(BOOTSTRAP)).status_code == 403


def test_branch_department_crud_and_dependent_objects(client, runtime):
    headers = auth("hr-a-key")
    department = client.post(
        "/api/admin/departments",
        headers=headers,
        json={"name": "Продажи", "branch_code": "A", "manager_id": "manager"},
    )
    assert department.status_code == 201, department.text
    item = department.json()
    updated = client.put(
        f"/api/admin/departments/{item['id']}",
        headers=headers,
        json={"name": "Коммерческий отдел", "branch_code": "A", "revision": item["revision"]},
    )
    assert updated.status_code == 200
    assert client.delete(f"/api/admin/departments/{item['id']}", headers=headers).status_code == 200
    assert client.post(f"/api/admin/departments/{item['id']}/restore", headers=headers).status_code == 200
    created = client.post(
        "/api/admin/employees",
        headers=headers,
        json=employee_body(department="Коммерческий отдел", department_id=item["id"]),
    )
    assert created.status_code == 201
    assert client.delete(f"/api/admin/departments/{item['id']}", headers=headers).status_code == 409
    assert client.delete("/api/admin/branches/A", headers=headers).status_code == 409
    branch = client.get("/api/admin/branches", headers=headers).json()[0]
    response = client.put(
        "/api/admin/branches/A",
        headers=headers,
        json={
            "code": "A",
            "name": "Обновлённая площадка",
            "timezone": "Asia/Novosibirsk",
            "resources": {"food": "Тестовая столовая"},
            "resource_labels": {"food": "Питание"},
            "revision": branch["revision"],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["resource_labels"]["food"] == "Питание"


def test_documents_metadata_replacement_archive_and_restore(client, runtime):
    headers = auth("hr-a-key")
    initial = client.post(
        "/api/documents",
        headers=headers,
        data={"title": "Регламент", "branch_codes": '["A"]'},
        files={"file": ("rules.txt", b"version1")},
    )
    assert initial.status_code == 201
    doc_id = initial.json()["id"]
    assert (
        client.put(
            f"/api/admin/documents/{doc_id}",
            headers=headers,
            json={"title": "Новый заголовок", "branch_codes": ["A"], "revision": 1},
        ).status_code
        == 200
    )
    new = client.post(
        f"/api/admin/documents/{doc_id}/version", headers=headers, files={"file": ("new.txt", b"version2")}
    )
    assert new.status_code == 201 and new.json()["version"] == 2
    current = client.get("/api/admin/documents", headers=headers).json()
    assert len(current) == 2  # The previous version stays available while the replacement is pending.
    assert client.get(f"/api/documents/{doc_id}/download", headers=headers).status_code == 200
    new_id = new.json()["id"]
    assert client.delete(f"/api/admin/documents/{new_id}", headers=headers).status_code == 200
    assert client.post(f"/api/admin/documents/{new_id}/restore", headers=headers).status_code == 200


def test_scenario_edit_creates_version_and_preserves_history(client, runtime):
    headers = auth("hr-a-key")
    body = {
        "name": "Адаптация A",
        "version": 1,
        "branch_codes": ["A"],
        "steps": [{"id": "welcome", "text": "Приветствие"}],
        "surveys": [],
    }
    first = client.post("/api/admin/scenarios", headers=headers, json=body)
    assert first.status_code == 201, first.text
    scenario_id = first.json()["id"]
    branch = client.get("/api/admin/branches", headers=headers).json()[0]
    assert (
        client.put(
            "/api/admin/branches/A",
            headers=headers,
            json={"code": "A", "name": "A", "scenario_id": scenario_id, "revision": branch["revision"]},
        ).status_code
        == 200
    )
    second = client.put(
        f"/api/admin/scenarios/{scenario_id}",
        headers=headers,
        json={**body, "revision": 1, "steps": [{"id": "welcome", "text": "Новый текст"}]},
    )
    assert second.status_code == 200, second.text
    assert second.json()["version"] == 2
    assert (
        client.put(
            f"/api/admin/scenarios/{scenario_id}", headers=headers, json={**body, "revision": 1}
        ).status_code
        == 409
    )
    assert client.delete(f"/api/admin/scenarios/{scenario_id}", headers=headers).status_code == 200
    assert client.delete(f"/api/admin/scenarios/{second.json()['id']}", headers=headers).status_code == 409
    assert client.post(f"/api/admin/scenarios/{scenario_id}/restore", headers=headers).status_code == 200
