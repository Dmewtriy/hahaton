"""Route attention signals without disclosing private answers to line managers."""

from sqlalchemy import select

from .access import in_scope
from .calendar import now
from .models import Employee, Identity, MeetingSignal
from .queue import send


def manager_for(session, employee):
    manager = session.get(Identity, employee.manager_id) if employee.manager_id else None
    if manager and manager.active and "manager" in manager.roles and in_scope(manager, employee.branch_code):
        return manager
    return None


def recommend(session, employee, analysis):
    signal = session.scalar(select(MeetingSignal).where(MeetingSignal.analysis_id == analysis.id))
    if not signal:
        signal = MeetingSignal(employee_id=employee.id, analysis_id=analysis.id, created_at=now(session))
        session.add(signal)
        session.flush()
    route(session, employee, signal)
    return signal


def route(session, employee, signal):
    manager = manager_for(session, employee)
    if not employee.active or not manager:
        signal.manager_id, signal.status = None, "awaiting_manager"
        return
    signal.manager_id, signal.status = manager.id, "queued"
    item = send(
        session,
        manager.id,
        f"Рекомендуется личная встреча с сотрудником {employee.name} ({employee.department}). "
        "Обратная связь требует внимания. Обсудите адаптацию лично; за рабочим контекстом обратитесь к HR. "
        "Рекомендация не является кадровым решением.",
        f"meeting:{signal.id}:{manager.id}",
        now(session),
    )
    item.body = {**item.body, "meeting_signal_id": signal.id}
    if item.status == "cancelled":
        item.status, item.retry_at = "pending", now(session)
    elif item.status == "sent":
        signal.status = "delivered"


def route_pending(session):
    for signal in session.scalars(
        select(MeetingSignal).where(MeetingSignal.status.in_(["pending", "awaiting_manager"])).limit(500)
    ):
        route(session, session.get(Employee, signal.employee_id), signal)


def delivery_allowed(session, item):
    signal = session.get(MeetingSignal, item.body["meeting_signal_id"])
    if not signal:
        return False
    employee = session.get(Employee, signal.employee_id)
    manager = manager_for(session, employee)
    if not employee.active or not manager or manager.id != item.recipient_id:
        signal.status = "pending"
        return False
    return True


def public_signal(signal, employee):
    return {
        "id": signal.id,
        "employee_id": employee.id,
        "employee_name": employee.name,
        "department": employee.department,
        "branch_code": employee.branch_code,
        "manager_id": signal.manager_id,
        "status": signal.status,
        "created_at": signal.created_at.isoformat(),
        "recommendation": "Провести личную встречу и обсудить адаптацию",
    }
