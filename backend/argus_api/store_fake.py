"""An in-memory DocumentStore for offline tests: the same contract as FirestoreStore, every write recorded.

`fail_on` makes a write to a matching path raise StoreError, to test how an unreachable Firestore surfaces.
"""

import copy
import threading
from collections.abc import Callable
from typing import Any

from argus_api.docstore import (
    AlreadyExists,
    Counted,
    Counter,
    CreateResult,
    NotFound,
    StoreError,
    Write,
    check_counters,
    check_path,
    counter_value,
)


class MemoryStore:
    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}
        self.writes: list[Write] = []  # every applied write, in order
        self.fail_on: Callable[[str], bool] = lambda path: False
        self._lock = threading.RLock()

    def _check_write(self, w: Write) -> None:
        check_path(w.path, document=True)
        if self.fail_on(w.path):
            raise StoreError(f"MemoryStore: scripted failure writing {w.path}")
        if w.op == "create" and w.path in self.docs:
            raise AlreadyExists(f"create {w.path}: already exists")
        if w.op == "update" and w.path not in self.docs:
            raise NotFound(f"update {w.path}: not found")

    def _apply(self, w: Write) -> None:
        data = copy.deepcopy(w.data)
        if w.op == "update":
            self.docs[w.path].update(data)
        else:
            self.docs[w.path] = data
        self.writes.append(w)

    def get(self, path: str) -> dict[str, Any] | None:
        check_path(path, document=True)
        with self._lock:
            doc = self.docs.get(path)
            return copy.deepcopy(doc) if doc is not None else None

    def set(self, path: str, data: dict[str, Any]) -> None:
        self.commit([Write("set", path, data)])

    def create(self, path: str, data: dict[str, Any]) -> None:
        self.commit([Write("create", path, data)])

    def update(self, path: str, data: dict[str, Any]) -> None:
        self.commit([Write("update", path, data)])

    def list_docs(self, collection_path: str) -> list[tuple[str, dict[str, Any]]]:
        depth = len(check_path(collection_path, document=False)) + 1
        prefix = collection_path + "/"
        with self._lock:
            return sorted(
                (p.rsplit("/", 1)[1], copy.deepcopy(d))
                for p, d in self.docs.items()
                if p.startswith(prefix) and len(p.split("/")) == depth
            )

    def commit(self, writes: list[Write]) -> None:
        with self._lock:
            for w in writes:
                self._check_write(w)
            for w in writes:
                self._apply(w)

    def create_counted(self, path: str, data: dict[str, Any], counters: list[Counter]) -> Counted:
        check_counters(counters)
        with self._lock:
            if path in self.docs:
                return Counted(CreateResult.EXISTS)
            writes = [Write("create", path, data)]
            for c in counters:
                doc = self.docs.get(c.path)
                count = counter_value(c, doc)
                if c.cap is not None and count >= c.cap:
                    return Counted(CreateResult.CAP_REACHED, c)
                writes.append(Write("set", c.path, {**(doc or {}), c.field: count + 1}))
            self.commit(writes)
            return Counted(CreateResult.CREATED)
