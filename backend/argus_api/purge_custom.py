"""Delete every pasted-clause run whose expires_at has passed, with all its documents.

  .venv/bin/python -m argus_api.purge_custom [--dry-run]

A custom run (Run.source "custom") carries expires_at, runs.CUSTOM_TTL after it started, on the run document
and on every steps/findings/audit document. The Firestore TTL policy (admin.py ttl) deletes those documents,
but only "typically within 24 hours" of expiry and without any promise; this command is the backstop that
deletes them now. It deletes:

  - every runs/{run_id} whose document has source "custom" and an expires_at at or before now, together with
    every document of its steps, findings and audit subcollections (children first, so an interrupted purge
    leaves the run to be found again next time);
  - every steps/findings/audit document with an expires_at at or before now whose run document is already gone
    (TTL deletes documents one by one, so it can remove a run before its children).

Sample runs, saved samples and anything without an expired expires_at are never touched. Only paths and counts
are printed, never document contents, so the clause text does not reach the operator's terminal or logs.

GOOGLE_CLOUD_PROJECT names the project; FIRESTORE_EMULATOR_HOST, when set, points at the emulator.
"""

import argparse
import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from google.cloud import firestore

from argus_api.runs import EXPIRES_AT, FIRESTORE_BATCH_LIMIT, RUN_CHILDREN, RUNS, run_path
from argus_api.schema import CUSTOM_SOURCE


class PurgeTarget(Protocol):
    def run_ids(self) -> list[str]:
        """Every id under runs/, including ids whose run document is gone but whose subcollections remain."""
        ...

    def get(self, path: str) -> dict[str, Any] | None: ...

    def list_docs(self, collection_path: str) -> list[tuple[str, dict[str, Any]]]: ...

    def delete(self, paths: list[str]) -> None: ...


@dataclass
class PurgeReport:
    runs: list[str] = field(default_factory=list)  # run ids whose run document was deleted
    orphans: list[str] = field(default_factory=list)  # run ids whose leftover children were deleted
    documents: int = 0  # every document deleted (or, in a dry run, that would be)


def expired(doc: dict[str, Any], now: datetime) -> bool:
    at = doc.get(EXPIRES_AT)
    return isinstance(at, datetime) and at <= now


def _children(target: PurgeTarget, run_id: str) -> list[tuple[str, dict[str, Any]]]:
    base = run_path(run_id)
    return [
        (f"{base}/{child}/{doc_id}", data)
        for child in RUN_CHILDREN
        for doc_id, data in target.list_docs(f"{base}/{child}")
    ]


def purge_expired(target: PurgeTarget, now: datetime, *, dry_run: bool = False) -> PurgeReport:
    """Delete (with dry_run, only count) every expired custom run and leftover child (module docstring)."""
    report = PurgeReport()
    for run_id in target.run_ids():
        run = target.get(run_path(run_id))
        if run is not None:
            if run.get("source") != CUSTOM_SOURCE or not expired(run, now):
                continue
            paths = [path for path, _ in _children(target, run_id)] + [run_path(run_id)]
            report.runs.append(run_id)
        else:
            paths = [path for path, data in _children(target, run_id) if expired(data, now)]
            if not paths:
                continue
            report.orphans.append(run_id)
        report.documents += len(paths)
        if not dry_run:
            target.delete(paths)
    return report


class FirestorePurgeTarget:
    def __init__(self, client: firestore.Client) -> None:
        self._db = client

    def run_ids(self) -> list[str]:
        # list_documents also yields "missing" documents: ids with subcollections but no document of their own
        return sorted(ref.id for ref in self._db.collection(RUNS).list_documents())

    def get(self, path: str) -> dict[str, Any] | None:
        snap = self._db.document(path).get()
        return snap.to_dict() if snap.exists else None

    def list_docs(self, collection_path: str) -> list[tuple[str, dict[str, Any]]]:
        return [(d.id, d.to_dict() or {}) for d in self._db.collection(collection_path).stream()]

    def delete(self, paths: list[str]) -> None:
        for chunk in _chunks(paths, FIRESTORE_BATCH_LIMIT):
            batch = self._db.batch()
            for path in chunk:
                batch.delete(self._db.document(path))
            batch.commit()


def _chunks(items: list[str], size: int) -> Iterable[list[str]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _client() -> firestore.Client:
    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
    if not project:
        raise SystemExit("GOOGLE_CLOUD_PROJECT must be set")
    return firestore.Client(project=project)


def main(argv: list[str] | None = None, *, now: datetime | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="count what would be deleted, delete nothing")
    args = ap.parse_args(argv)
    report = purge_expired(FirestorePurgeTarget(_client()), now or datetime.now(UTC), dry_run=args.dry_run)
    verb = "would delete" if args.dry_run else "deleted"
    print(
        f"{verb} {report.documents} document(s): {len(report.runs)} expired custom run(s) with their "
        f"documents, leftover documents of {len(report.orphans)} run(s) already gone"
    )
    for run_id in report.runs:
        print(f"  {run_path(run_id)}")
    for run_id in report.orphans:
        print(f"  {run_path(run_id)} (children only)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
