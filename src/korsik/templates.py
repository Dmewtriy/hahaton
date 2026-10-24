"""Explicit safe placeholders; no attribute traversal or executable expressions."""

import re

TOKENS = {
    "name": "Имя сотрудника",
    "branch": "Площадка",
    "department": "Подразделение",
    "recruiter_name": "Закреплённый HR",
    "manager_name": "Руководитель",
    "hire_date": "Дата выхода",
    "probation_end": "Конец адаптации",
    "timezone": "Часовой пояс",
}
PATTERN = re.compile(r"\{([A-Za-z_][A-Za-z_0-9]*)\}")


def render(session, employee, text):
    from .models import Branch, Identity

    branch = session.get(Branch, employee.branch_code)
    hr = session.get(Identity, employee.recruiter_id)
    manager = session.get(Identity, employee.manager_id) if employee.manager_id else None
    values = {
        "name": employee.name,
        "branch": branch.name,
        "department": employee.department,
        "recruiter_name": hr.name if hr else "HR не назначен",
        "manager_name": manager.name if manager else "Руководитель не назначен",
        "hire_date": employee.hire_date.isoformat(),
        "probation_end": employee.probation_end.isoformat(),
        "timezone": employee.timezone,
    }
    return PATTERN.sub(lambda match: values.get(match[1], match[0]), text)
