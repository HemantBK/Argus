"""ops/budget_guard/guard.py: switches live reviews off when the judging-period budget is spent."""

import base64
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

GUARD = Path(__file__).resolve().parents[2] / "ops" / "budget_guard" / "guard.py"
spec = importlib.util.spec_from_file_location("budget_guard", GUARD)
assert spec and spec.loader
guard = importlib.util.module_from_spec(spec)
sys.modules["budget_guard"] = guard
spec.loader.exec_module(guard)


class FakeDoc:
    def __init__(self, data: dict[str, Any] | None) -> None:
        self.data = data

    def get(self) -> "FakeDoc":
        return self

    @property
    def exists(self) -> bool:
        return self.data is not None

    def update(self, fields: dict[str, Any]) -> None:
        assert self.data is not None
        self.data.update(fields)


class FakeDb:
    def __init__(self, docs: dict[str, dict[str, Any] | None]) -> None:
        self.docs = {k: FakeDoc(v) for k, v in docs.items()}

    def document(self, path: str) -> FakeDoc:
        return self.docs.setdefault(path, FakeDoc(None))


def push(cost: Any, budget: Any = 1000.0) -> bytes:
    note = {
        "budgetDisplayName": "argus-judging",
        "costAmount": cost,
        "budgetAmount": budget,
        "currencyCode": "INR",
    }
    data = base64.b64encode(json.dumps(note).encode()).decode()
    return json.dumps({"message": {"data": data, "messageId": "1"}, "subscription": "s"}).encode()


LIMITS = {"daily_run_cap": 100, "hourly_run_cap": 20, "per_user_daily_run_cap": 5, "enabled": True}


def test_spent_budget_switches_both_off_and_keeps_the_other_fields() -> None:
    db = FakeDb(
        {
            "config/limits": dict(LIMITS),
            "config/custom_limits": {"custom_enabled": True, "custom_daily_cap": 20},
        }
    )
    event = guard.handle(push(1000.0), db)
    assert event["spent"] and len(event["written"]) == 2
    assert db.docs["config/limits"].data == {**LIMITS, "enabled": False}
    assert db.docs["config/custom_limits"].data == {"custom_enabled": False, "custom_daily_cap": 20}


def test_below_budget_writes_nothing() -> None:
    db = FakeDb(
        {
            "config/limits": dict(LIMITS),
            "config/custom_limits": {"custom_enabled": True, "custom_daily_cap": 20},
        }
    )
    event = guard.handle(push(999.99), db)
    assert not event["spent"] and event["written"] == []
    assert db.docs["config/limits"].data["enabled"] is True


def test_a_missing_custom_document_is_not_created() -> None:
    db = FakeDb({"config/limits": dict(LIMITS)})
    guard.handle(push(1500.0), db)
    assert not db.docs["config/custom_limits"].exists


@pytest.mark.parametrize("cost, budget", [(None, 1000.0), ("900", 1000.0), (5.0, 0), (5.0, None)])
def test_a_malformed_notification_is_refused(cost: Any, budget: Any) -> None:
    with pytest.raises(ValueError):
        guard.handle(push(cost, budget), FakeDb({}))
