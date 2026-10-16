"""The few Firestore operations the API needs, behind one small interface.

Paths are slash-separated document paths ("runs/abc", "runs/abc/steps/0001"). Data is plain JSON-ready dicts.
FirestoreStore is the real adapter (Firestore in cloud mode, the emulator locally); MemoryStore in
store_fake.py implements the same contract for offline tests. Every Firestore failure surfaces as StoreError,
never swallowed; an attempt to create a document that exists is AlreadyExists.
"""

import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal, Protocol, TypeVar

from google.api_core import exceptions as gexc
from google.cloud import firestore

T = TypeVar("T")
WriteOp = Literal["create", "set", "update"]


class StoreError(RuntimeError):
    """Firestore could not complete an operation."""


class AlreadyExists(StoreError):
    pass


class Busy(StoreError):
    """Too many concurrent writers on the same documents; the caller should try again shortly."""


class NotFound(StoreError):
    pass


@dataclass(frozen=True)
class Write:
    op: WriteOp  # create fails if the document exists; update fails if it does not
    path: str
    data: dict[str, Any]


class CreateResult(StrEnum):
    CREATED = "created"
    EXISTS = "exists"  # the document was already there; nothing written
    CAP_REACHED = "cap_reached"  # a counter was at its cap; nothing written


@dataclass(frozen=True)
class Counter:
    """An integer `field` of the document at `path`, allowed to grow up to `cap` (None: no cap)."""

    path: str
    field: str
    cap: int | None


@dataclass(frozen=True)
class Counted:
    result: CreateResult
    full: Counter | None = None  # with CAP_REACHED: the first counter that was at its cap


class DocumentStore(Protocol):
    def get(self, path: str) -> dict[str, Any] | None: ...

    def set(self, path: str, data: dict[str, Any]) -> None: ...

    def create(self, path: str, data: dict[str, Any]) -> None:
        """Write a new document; AlreadyExists if it is there."""
        ...

    def update(self, path: str, data: dict[str, Any]) -> None:
        """Merge top-level fields into an existing document; NotFound if it is missing."""
        ...

    def list_docs(self, collection_path: str) -> list[tuple[str, dict[str, Any]]]:
        """(doc id, data) for every document directly in the collection, ordered by doc id."""
        ...

    def commit(self, writes: list[Write]) -> None:
        """All writes or none (AlreadyExists / NotFound / StoreError)."""
        ...

    def create_counted(self, path: str, data: dict[str, Any], counters: list[Counter]) -> Counted:
        """In one transaction: create `path` unless it exists, and add 1 to every counter unless one of them
        is already at its cap. Either all of it happens or none of it."""
        ...


def check_counters(counters: list[Counter]) -> None:
    paths = [c.path for c in counters]
    if not counters or len(set(paths)) != len(paths):
        raise ValueError(f"create_counted needs counters on distinct documents, got {paths}")
    for c in counters:
        check_path(c.path, document=True)


def counter_value(c: Counter, doc: dict[str, Any] | None) -> int:
    count = (doc or {}).get(c.field, 0)
    if not isinstance(count, int) or isinstance(count, bool):
        raise StoreError(f"{c.path}.{c.field} is {count!r}, not an integer")
    return count


def check_path(path: str, *, document: bool) -> list[str]:
    parts = path.split("/")
    if any(not p or p in (".", "..") or p.startswith("__") for p in parts):
        raise ValueError(f"unusable Firestore path {path!r}")
    if (len(parts) % 2 == 0) != document:
        raise ValueError(f"{path!r} is not a {'document' if document else 'collection'} path")
    return parts


def _wrap(what: str, call: Callable[[], T]) -> T:
    try:
        return call()
    except gexc.Conflict as e:  # AlreadyExists is a Conflict (409)
        raise AlreadyExists(f"{what}: already exists") from e
    except gexc.NotFound as e:
        raise NotFound(f"{what}: not found") from e
    except gexc.GoogleAPIError as e:
        raise StoreError(f"Firestore {what} failed: {e}") from e


# create_counted: outer tries when concurrent transactions keep aborting, and the base wait before retry n.
COUNTED_TRIES = 6
COUNTED_BACKOFF_S = 0.1


class FirestoreStore:
    def __init__(self, client: firestore.Client, *, sleep: Callable[[float], None] = time.sleep) -> None:
        self._db = client
        self._sleep = sleep

    def _doc(self, path: str) -> firestore.DocumentReference:
        check_path(path, document=True)
        return self._db.document(path)

    def get(self, path: str) -> dict[str, Any] | None:
        snap = _wrap(f"get {path}", lambda: self._doc(path).get())
        return snap.to_dict() if snap.exists else None

    def set(self, path: str, data: dict[str, Any]) -> None:
        _wrap(f"set {path}", lambda: self._doc(path).set(data))

    def create(self, path: str, data: dict[str, Any]) -> None:
        _wrap(f"create {path}", lambda: self._doc(path).create(data))

    def update(self, path: str, data: dict[str, Any]) -> None:
        _wrap(f"update {path}", lambda: self._doc(path).update(data))

    def list_docs(self, collection_path: str) -> list[tuple[str, dict[str, Any]]]:
        check_path(collection_path, document=False)

        def stream() -> list[tuple[str, dict[str, Any]]]:
            docs = self._db.collection(collection_path).stream()
            return sorted(((d.id, d.to_dict() or {}) for d in docs), key=lambda item: item[0])

        return _wrap(f"list {collection_path}", stream)

    def commit(self, writes: list[Write]) -> None:
        batch = self._db.batch()
        for w in writes:
            ref = self._doc(w.path)
            if w.op == "create":
                batch.create(ref, w.data)
            elif w.op == "set":
                batch.set(ref, w.data)
            else:
                batch.update(ref, w.data)
        _wrap(f"commit of {len(writes)} write(s)", batch.commit)

    def create_counted(self, path: str, data: dict[str, Any], counters: list[Counter]) -> Counted:
        check_counters(counters)
        ref = self._doc(path)
        refs = [self._doc(c.path) for c in counters]

        @firestore.transactional
        def attempt(tx: firestore.Transaction) -> Counted:
            # Reads first (a Firestore transaction rule), then writes.
            if ref.get(transaction=tx).exists:
                return Counted(CreateResult.EXISTS)
            counts = []
            for c, cref in zip(counters, refs, strict=True):
                snap = cref.get(transaction=tx)
                count = counter_value(c, snap.to_dict() if snap.exists else None)
                if c.cap is not None and count >= c.cap:
                    return Counted(CreateResult.CAP_REACHED, c)
                counts.append(count)
            tx.create(ref, data)
            for c, cref, count in zip(counters, refs, counts, strict=True):
                tx.set(cref, {c.field: count + 1}, merge=True)
            return Counted(CreateResult.CREATED)

        # Every admission updates the same counter documents, so concurrent admissions abort each other's
        # transactions. The client library retries only immediately; spread the outer retries with jitter so
        # the contenders take turns, and report Busy (a clean 503) if they still cannot get through.
        for i in range(COUNTED_TRIES):
            try:
                result: Counted = _wrap(
                    f"create {path} with counters", lambda: attempt(self._db.transaction())
                )
                return result
            except ValueError as e:
                if not isinstance(e.__cause__, gexc.Aborted):
                    raise
            if i < COUNTED_TRIES - 1:
                self._sleep(random.uniform(0.5, 1.5) * COUNTED_BACKOFF_S * 2**i)
        raise Busy(f"create {path} with counters: still contended after {COUNTED_TRIES} tries")
