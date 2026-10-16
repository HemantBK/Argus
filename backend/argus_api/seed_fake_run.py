"""Write one clearly labelled FAKE run to Firestore so the web app can be built before the agent exists.

Usage (local, emulator running):  make seed
Every text field says FAKE; nothing here is an RBI fact or a measured result.
"""

import hashlib
from datetime import UTC, datetime, timedelta

from google.cloud import firestore

from argus_api.schema import (
    AuditEvent,
    Check,
    CheckName,
    Citation,
    Finding,
    FindingLabel,
    MonitorAction,
    MonitorStatus,
    Run,
    RunStatus,
    RunSummary,
    Step,
    StepKind,
    Verdict,
)

RUN_ID = "fake-run-0001"


def _citation(para: str, in_force: bool) -> Citation:
    quote = f"FAKE quoted RBI text for para {para}."
    return Citation(
        instrument="FAKE instrument",
        instrument_ref="FAKE/REF",
        para=para,
        url="https://www.rbi.org.in/",
        quote=quote,
        snapshot_date="2026-10-16",
        content_hash=hashlib.sha256(quote.encode()).hexdigest(),
        in_force=in_force,
    )


def build() -> tuple[Run, list[Step], list[Finding], list[AuditEvent]]:
    t0 = datetime.now(UTC)
    run = Run(
        run_id=RUN_ID,
        owner_uid="fake-owner",
        policy_id="sample-a",
        policy_title="FAKE sample policy A",
        saved=True,
        status=RunStatus.DONE,
        started_at=t0,
        finished_at=t0 + timedelta(seconds=12),
        agent_model="fake-agent-model",
        monitor_model="fake-monitor-model",
        corpus_as_on="2026-10-16",
        summary=RunSummary(clauses=2, outdated_reference=1, compliant=1, monitor_revisions=1),
        latency_ms=12000,
    )
    steps = [
        Step(
            seq=1, clause_id="c1", kind=StepKind.SEARCH, summary="FAKE search", tool="search_library", at=t0
        ),
        Step(
            seq=2,
            clause_id="c1",
            kind=StepKind.MONITOR,
            summary="FAKE: cited paragraph does not exist; revise",
            verdict=Verdict(
                action=MonitorAction.REVISE,
                checks=[Check(name=CheckName.PARAGRAPH_EXISTS, passed=False, detail="FAKE")],
                reason="FAKE reason",
            ),
            at=t0 + timedelta(seconds=3),
        ),
        Step(seq=3, clause_id="c1", kind=StepKind.FINAL, summary="FAKE final", at=t0 + timedelta(seconds=6)),
    ]
    findings = [
        Finding(
            clause_id="c1",
            clause_text="FAKE clause citing a withdrawn instrument.",
            label=FindingLabel.OUTDATED_REFERENCE,
            rationale="FAKE rationale",
            citation=_citation("10(4)", in_force=True),
            replaced_citation=_citation("9", in_force=False),
            monitor_status=MonitorStatus.REVISED,
        ),
        Finding(
            clause_id="c2",
            clause_text="FAKE compliant clause.",
            label=FindingLabel.COMPLIANT,
            rationale="FAKE rationale",
            citation=_citation("11", in_force=True),
            monitor_status=MonitorStatus.PASSED,
        ),
    ]
    audit = [AuditEvent(seq=1, at=t0, actor="agent", event="FAKE run started")]
    return run, steps, findings, audit


def write(db: firestore.Client) -> None:
    run, steps, findings, audit = build()
    for root in ("runs", "samples"):
        ref = db.collection(root).document(run.run_id)
        ref.set(run.model_dump(mode="json"))
        for s in steps:
            ref.collection("steps").document(f"{s.seq:04d}").set(s.model_dump(mode="json"))
        for f in findings:
            ref.collection("findings").document(f.clause_id).set(f.model_dump(mode="json"))
        for a in audit:
            ref.collection("audit").document(f"{a.seq:04d}").set(a.model_dump(mode="json"))


if __name__ == "__main__":
    import os

    write(firestore.Client(project=os.environ["GOOGLE_CLOUD_PROJECT"]))
    print(f"Seeded {RUN_ID} into runs/ and samples/")
