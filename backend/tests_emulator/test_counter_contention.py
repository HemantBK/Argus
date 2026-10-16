"""Concurrent admissions on the same counter documents (live demo: several judges clicking at once)."""

import os
import uuid
from concurrent.futures import ThreadPoolExecutor

from google.cloud import firestore

from argus_api.docstore import Counter, CreateResult, FirestoreStore

CONCURRENT = 8


def _admit_all(cap: int | None) -> list[CreateResult]:
    store = FirestoreStore(firestore.Client(project=os.environ.get("GOOGLE_CLOUD_PROJECT", "argus-local")))
    tag = uuid.uuid4().hex
    counters = [Counter(f"test_usage/{tag}-day", "runs", cap), Counter(f"test_usage/{tag}-hour", "runs", None)]

    def admit(i: int) -> CreateResult:
        return store.create_counted(f"test_runs/{tag}-{i}", {"i": i}, counters).result

    with ThreadPoolExecutor(CONCURRENT) as ex:
        return list(ex.map(admit, range(CONCURRENT)))


def test_concurrent_admissions_all_get_through_without_a_cap() -> None:
    assert _admit_all(cap=None) == [CreateResult.CREATED] * CONCURRENT


def test_concurrent_admissions_respect_the_cap_exactly() -> None:
    cap = 3
    results = _admit_all(cap=cap)
    assert results.count(CreateResult.CREATED) == cap
    assert results.count(CreateResult.CAP_REACHED) == CONCURRENT - cap
