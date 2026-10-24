"""MAX launch signature verification and short-lived, revocable application sessions."""

import hashlib
import hmac
import json
import re
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl

from fastapi import HTTPException
from sqlalchemy import select

from .access import is_admin, new_token, token_hash
from .db import utcnow
from .models import Identity, WebSession


def _pairs(raw):
    try:
        pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True, errors="strict")
    except (ValueError, UnicodeError):
        raise HTTPException(401, "Некорректные данные запуска MAX") from None
    if not pairs or len(pairs) != len({key for key, _ in pairs}):
        raise HTTPException(401, "Повторяющиеся параметры запуска MAX")
    return dict(pairs)


def validate_init_data(raw, bot_token, max_age_seconds=3600, current_time=None):
    if not bot_token or not raw or len(raw) > 16384:
        raise HTTPException(401, "Данные запуска MAX отсутствуют")
    if raw.startswith("#") or raw.startswith("WebAppData="):
        outer = _pairs(raw.lstrip("#"))
        raw = outer.get("WebAppData", "")
    values = _pairs(raw)
    signature = values.pop("hash", "")
    if not re.fullmatch(r"[a-fA-F0-9]{64}", signature):
        raise HTTPException(401, "Подпись MAX отсутствует")
    if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) for key in values):
        raise HTTPException(401, "Некорректные параметры MAX")
    check = "\n".join(f"{key}={value}" for key, value in sorted(values.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature.lower()):
        raise HTTPException(401, "Не удалось подтвердить запуск из MAX")
    try:
        auth_date = int(values["auth_date"])
        user = json.loads(values["user"])
        user_id = user["id"]
        if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise HTTPException(401, "Некорректный пользователь MAX") from None
    current_time = current_time or utcnow()
    age = current_time.timestamp() - auth_date
    if age < -30 or age > max_age_seconds:
        raise HTTPException(401, "Сеанс запуска устарел. Откройте кабинет заново из MAX")
    return str(user_id), datetime.fromtimestamp(auth_date, UTC) + timedelta(seconds=max_age_seconds)


def launch_session(session, settings, raw):
    user_id, expires_at = validate_init_data(raw, settings.max_token, settings.webapp_auth_max_age_seconds)
    actor = session.scalar(select(Identity).where(Identity.max_user_id == user_id))
    if not actor or not is_admin(actor):
        raise HTTPException(403, "Для вашего аккаунта MAX нет доступа к кабинету HR")
    return issue_session(session, actor, "max", expires_at)


def issue_session(session, actor, source, expires_at=None):
    if not is_admin(actor):
        raise HTTPException(403, "Кабинет доступен только администраторам и HR")
    token = new_token()
    expires_at = expires_at or utcnow() + timedelta(hours=1)
    session.add(
        WebSession(token_hash=token_hash(token), identity_id=actor.id, source=source, expires_at=expires_at)
    )
    return {"session_token": token, "expires_at": expires_at.isoformat()}
