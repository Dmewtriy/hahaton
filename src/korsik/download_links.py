"""Short-lived, document-scoped links for MAX's native downloadFile bridge."""

import base64
import hashlib
import hmac
import json
import time

from fastapi import HTTPException


def issue(secret, identity_id, document_id):
    payload = json.dumps(
        {"actor": identity_id, "document": document_id, "expires": int(time.time()) + 60},
        separators=(",", ":"),
    ).encode()
    encoded = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    signature = hmac.new(secret.encode(), ("download:" + encoded).encode(), hashlib.sha256).hexdigest()
    return encoded + "." + signature


def validate(secret, token, document_id):
    try:
        encoded, signature = token.split(".")
        expected = hmac.new(secret.encode(), ("download:" + encoded).encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise ValueError()
        payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        if payload["document"] != document_id or payload["expires"] < time.time():
            raise ValueError()
        return payload["actor"]
    except (ValueError, KeyError, TypeError, UnicodeError):
        raise HTTPException(401, "Ссылка устарела. Скачайте документ заново из кабинета") from None
