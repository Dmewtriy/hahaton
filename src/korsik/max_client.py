import hashlib
import json
import ssl
from urllib.parse import urlparse

import httpx


class DeliveryError(Exception):
    def __init__(self, code, *, retryable=False, uncertain=False):
        self.code, self.retryable, self.uncertain = code, retryable, uncertain
        super().__init__(code)


def normalize_update(update):
    kind = update.get("update_type")
    if kind == "message_created":
        message = update.get("message", {})
        # Never bind or answer from a group: onboarding includes private employee data.
        if message.get("recipient", {}).get("chat_type") not in {None, "dialog"}:
            return None
        if message.get("sender", {}).get("is_bot"):
            return None
        body = message.get("body", {})
        user = message.get("sender", {}).get("user_id")
        event_id = body.get("mid")
        payload = {"kind": "message", "text": body.get("text") or "", "payload": None}
    elif kind == "message_callback":
        if update.get("message", {}).get("recipient", {}).get("chat_type") not in {None, "dialog"}:
            return None
        callback = update.get("callback", {})
        user = callback.get("user", {}).get("user_id")
        event_id = callback.get("callback_id")
        payload = {
            "kind": "callback",
            "text": "",
            "payload": callback.get("payload"),
            "callback_id": event_id,
        }
    elif kind in {"bot_started", "bot_stopped"}:
        user = update.get("user", {}).get("user_id")
        raw = json.dumps(update, sort_keys=True, ensure_ascii=False)
        event_id = hashlib.sha256(raw.encode()).hexdigest()
        payload = {
            "kind": "start" if kind == "bot_started" else "stop",
            "text": "",
            "payload": update.get("payload"),
        }
    else:
        return None
    if user is None or not event_id:
        return None
    payload.update(max_user_id=str(user), event_id=f"max:{kind}:{event_id}")
    return payload


class MockMAX:
    def __init__(self):
        self.deliveries = []

    def send(self, user_id, body, attachments=None):
        result = {"user_id": user_id, "body": body, "attachments": attachments or []}
        self.deliveries.append(result)
        return f"mock-{len(self.deliveries)}"

    def acknowledge(self, callback_id):
        pass

    def upload(self, path, kind="file"):
        return {"type": kind, "payload": {"token": "mock:" + path.name}}

    def close(self):
        pass


class MAXClient:
    def __init__(self, settings, client=None):
        verify = ssl.create_default_context(cafile=settings.max_ca_file) if settings.max_ca_file else True
        self.client = client or httpx.Client(
            base_url=settings.max_base_url.rstrip("/"),
            headers={"Authorization": settings.max_token},
            timeout=httpx.Timeout(20, connect=5),
            verify=verify,
        )

    def send(self, user_id, body, attachments=None):
        message = {"text": body["text"]}
        media = list(attachments or [])
        if body.get("buttons"):
            media.append(
                {
                    "type": "inline_keyboard",
                    "payload": {
                        "buttons": [
                            [{"type": "callback", "text": b["text"], "payload": b["payload"]}]
                            for b in body["buttons"]
                        ]
                    },
                }
            )
        if media:
            message["attachments"] = media
        try:
            response = self.client.post("/messages", params={"user_id": user_id}, json=message)
        except httpx.ConnectError as exc:
            raise DeliveryError("connect_failed", retryable=True) from exc
        except httpx.HTTPError as exc:
            # A timeout after sending may mean MAX already accepted the message.
            raise DeliveryError("delivery_unknown", uncertain=True) from exc
        if response.status_code == 429:
            raise DeliveryError("rate_limited", retryable=True)
        if response.status_code >= 500:
            raise DeliveryError("server_delivery_unknown", uncertain=True)
        if response.is_error:
            try:
                code = response.json().get("code", "rejected")
            except ValueError:
                code = "rejected"
            raise DeliveryError(str(code)[:100], retryable=code == "attachment.not.ready")
        try:
            return response.json()["message"]["body"]["mid"]
        except (ValueError, KeyError, TypeError) as exc:
            raise DeliveryError("invalid_success_response", uncertain=True) from exc

    def acknowledge(self, callback_id):
        response = self.client.post("/answers", params={"callback_id": callback_id}, json={})
        response.raise_for_status()

    def upload(self, path, kind="file"):
        response = self.client.post("/uploads", params={"type": kind})
        response.raise_for_status()
        info = response.json()
        url = info["url"]
        parsed = urlparse(url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or not any(
                parsed.hostname == domain or parsed.hostname.endswith("." + domain)
                for domain in ("max.ru", "oneme.ru")
            )
        ):
            raise DeliveryError("untrusted_upload_host")
        # Signed upload URL is used without leaking the bot authorization header.
        with path.open("rb") as stream, httpx.Client(timeout=60, follow_redirects=False) as client:
            uploaded = client.post(url, files={"data": (path.name, stream)})
        uploaded.raise_for_status()
        data = uploaded.json()
        if kind == "image" and "photos" in data:
            return {"type": "image", "payload": {"photos": data["photos"]}}
        token = data.get("token") or info.get("token")
        if not token:
            raise DeliveryError("missing_upload_token")
        return {"type": kind, "payload": {"token": token}}

    def get_updates(self, marker=None):
        params = {
            "timeout": 30,
            "limit": 100,
            "types": "message_created,message_callback,bot_started,bot_stopped",
        }
        if marker is not None:
            params["marker"] = marker
        response = self.client.get("/updates", params=params, timeout=40)
        response.raise_for_status()
        return response.json()

    def subscribe(self, url, secret):
        response = self.client.post(
            "/subscriptions",
            json={
                "url": url,
                "secret": secret,
                "update_types": ["message_created", "message_callback", "bot_started", "bot_stopped"],
            },
        )
        response.raise_for_status()
        if not response.json().get("success"):
            raise DeliveryError("subscription_rejected")
        return response.json()

    def close(self):
        self.client.close()
