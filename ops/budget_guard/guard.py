"""Budget guard: switch live reviews off when the judging-period budget is spent.

A Cloud Billing budget (scripts in ops/budget_guard/README.md) publishes its status to the Pub/Sub topic
argus-budget several times a day. A push subscription delivers each message here. When the reported cost has
reached the budget amount, the guard sets config/limits.enabled and config/custom_limits.custom_enabled to
false, so the API refuses new live reviews and pasted clauses (503) while the saved runs, which need no Gemini
call, keep working. It never switches anything back on: that is a person's decision (argus_api.admin).

Billing data reaches budgets a few hours late, so spending can pass the amount by a few hours of use before
this fires; the API's daily and hourly run caps bound that overshoot.
"""

import base64
import json
import logging
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

log = logging.getLogger("argus.budget_guard")

# The fields this guard turns off. Only these fields are written, so the rest of each document is kept.
SWITCHES = (("config/limits", "enabled"), ("config/custom_limits", "custom_enabled"))


def budget_spent(notification: dict[str, Any]) -> bool:
    """True when a Cloud Billing budget notification reports cost at or above the budget amount."""
    cost, budget = notification.get("costAmount"), notification.get("budgetAmount")
    if not isinstance(cost, (int, float)) or not isinstance(budget, (int, float)) or budget <= 0:
        raise ValueError(f"not a budget notification: costAmount={cost!r} budgetAmount={budget!r}")
    return cost >= budget


def decode_push(body: bytes) -> dict[str, Any]:
    """The budget notification inside a Pub/Sub push request."""
    envelope = json.loads(body)
    data = envelope["message"]["data"]
    notification: dict[str, Any] = json.loads(base64.b64decode(data))
    return notification


def switch_off(db: Any) -> list[str]:
    """Set every switch to false in documents that exist; return what was written."""
    written = []
    for path, field in SWITCHES:
        ref = db.document(path)
        if ref.get().exists:
            ref.update({field: False})
            written.append(f"{path}.{field}=false")
    return written


def handle(body: bytes, db: Any) -> dict[str, Any]:
    notification = decode_push(body)
    spent = budget_spent(notification)
    written = switch_off(db) if spent else []
    event = {
        "message": "budget_guard",
        "costAmount": notification["costAmount"],
        "budgetAmount": notification["budgetAmount"],
        "currencyCode": notification.get("currencyCode"),
        "spent": spent,
        "written": written,
    }
    log.warning(json.dumps(event)) if spent else log.info(json.dumps(event))
    return event


class _Handler(BaseHTTPRequestHandler):
    db: Any = None

    def do_POST(self) -> None:  # noqa: N802 (http.server naming)
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        try:
            event = handle(body, self.db)
            self._reply(200, event)
        except (ValueError, KeyError, json.JSONDecodeError) as e:
            # A malformed message is acknowledged (204) so Pub/Sub does not redeliver it forever.
            log.error(json.dumps({"message": "budget_guard_bad_message", "error": str(e)[:200]}))
            self._reply(204, None)

    def _reply(self, status: int, payload: Any) -> None:
        self.send_response(status)
        self.end_headers()
        if payload is not None:
            self.wfile.write(json.dumps(payload).encode())


def main() -> None:
    from google.cloud import firestore

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    _Handler.db = firestore.Client()
    HTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8080"))), _Handler).serve_forever()


if __name__ == "__main__":
    main()
