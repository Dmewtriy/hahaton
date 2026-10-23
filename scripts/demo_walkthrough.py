"""Exercise an already running LOCAL demo server through HTTP, without a MAX client."""

import argparse
import json
from datetime import datetime, timedelta
from uuid import uuid4

import httpx

from korsik.config import Settings

parser = argparse.ArgumentParser()
parser.add_argument("--url", default="http://127.0.0.1:8000")
args = parser.parse_args()
settings = Settings()
if not settings.enable_demo or settings.environment == "production":
    raise SystemExit("This script requires an isolated demo environment")
credentials = json.loads((settings.data_dir / "demo_credentials.json").read_text(encoding="utf-8"))
emp = credentials["employee"]
with httpx.Client(
    base_url=args.url, headers={"Authorization": "Bearer " + settings.bootstrap_token}, timeout=60
) as client:

    def request(method, path, body=None, headers=None):
        response = client.request(method, path, json=body, headers=headers)
        response.raise_for_status()
        return response.json()

    def tick():
        request("POST", "/api/demo/tick?limit=500")

    def event(text="", kind="message", payload=None):
        request(
            "POST",
            "/api/demo/events",
            {
                "event_id": uuid4().hex,
                "max_user_id": emp["max_user_id"],
                "kind": kind,
                "text": text,
                "payload": payload,
            },
        )
        tick()

    request("PUT", "/api/demo/clock", {"now": emp["hire_date"] + "T10:30:00+03:00"})
    event(kind="start", payload=emp["invitation_token"])
    event("/ask Как открыть портал?")
    hr_headers = {"Authorization": "Bearer " + credentials["hr"]["api_token"]}

    # Complete any current choice/rating surveys, then advance to the third-day survey.
    def finish_surveys():
        for _ in range(120):
            runs = request("GET", f"/api/employees/{emp['id']}/surveys", headers=hr_headers)
            active = next((r for r in runs if r["status"] == "active"), None)
            if not active:
                break
            outbox = request("GET", "/api/demo/outbox?limit=1000")
            prompt = next(
                (
                    m
                    for m in outbox
                    if m["recipient_id"] == emp["identity_id"]
                    and m["body"].get("buttons")
                    and any(
                        b["payload"].startswith(f"a:{active['id']}:{active['position']}:")
                        for b in m["body"]["buttons"]
                    )
                ),
                None,
            )
            if active.get("awaiting_comment"):
                event("Демонстрационный комментарий")
            elif prompt:
                event(kind="callback", payload=prompt["body"]["buttons"][0]["payload"])
                event("/done")
            else:
                event("8")
        else:
            raise RuntimeError("Survey walkthrough did not converge")

    finish_surveys()
    schedule = request("GET", f"/api/employees/{emp['id']}/schedule", headers=hr_headers)
    third_day = next(s for s in schedule if s["step_key"] == "day3" and s["status"] != "cancelled")
    request(
        "PUT",
        "/api/demo/clock",
        {"now": (datetime.fromisoformat(third_day["due_at"]) + timedelta(minutes=1)).isoformat()},
    )
    tick()
    event("/hr Нужна помощь с адаптацией")
    inbox = request("GET", "/api/tickets", headers=hr_headers)
    ticket = next(t for t in inbox if t["employee_id"] == emp["id"] and t["human_mode"])
    request(
        "POST",
        f"/api/tickets/{ticket['id']}/reply",
        {"text": "Рекрутер подключился. Помогу разобраться."},
        headers={**hr_headers, "Idempotency-Key": uuid4().hex},
    )
    request("POST", f"/api/tickets/{ticket['id']}/close", headers=hr_headers)
    tick()
    finish_surveys()
    report = request("GET", "/api/dashboard", headers=hr_headers)
    print(
        json.dumps(
            {
                "employee_id": emp["id"],
                "totals": report["totals"],
                "checked": ["binding", "schedule", "surveys", "ML stub", "HR reply", "return"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
