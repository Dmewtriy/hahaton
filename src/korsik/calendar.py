import calendar
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .db import utcnow
from .models import Branch, Employee, State
from .schemas import Schedule


def now(session):
    state = session.get(State, "clock")
    return datetime.fromisoformat(state.value["now"]) if state and state.value.get("now") else utcnow()


def add_months(value: date, months: int):
    month_index = value.year * 12 + value.month - 1 + months
    year, month = divmod(month_index, 12)
    return date(year, month + 1, min(value.day, calendar.monthrange(year, month + 1)[1]))


def is_workday(day: date, branch: Branch):
    if day.isoformat() in branch.working_dates:
        return True
    return day.weekday() in branch.workdays and day.isoformat() not in branch.holidays


def next_workday(day: date, branch: Branch):
    for _ in range(370):
        if is_workday(day, branch):
            return day
        day += timedelta(days=1)
    raise ValueError("Calendar has no working dates within a year")


def due_date(employee: Employee, branch: Branch, rule: Schedule):
    anchor = employee.hire_date if rule.anchor == "hire" else employee.probation_end
    day = add_months(anchor, rule.months) + timedelta(days=rule.days)
    if rule.working_day is not None:
        day = next_workday(day, branch)
        for _ in range(rule.working_day - 1):
            day = next_workday(day + timedelta(days=1), branch)
    elif rule.next_workday:
        day = next_workday(day, branch)
    local = datetime.combine(day, time.fromisoformat(rule.at), ZoneInfo(employee.timezone))
    # Reject non-existent local times instead of silently shifting a scheduled event.
    utc = local.astimezone(UTC)
    if utc.astimezone(ZoneInfo(employee.timezone)).replace(tzinfo=None) != local.replace(tzinfo=None):
        raise ValueError("Scheduled local time falls into a DST gap")
    return utc
