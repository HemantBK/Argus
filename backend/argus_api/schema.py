"""Data contract between the API, Firestore and the web app (schema v0).

Firestore layout:
  runs/{run_id}                    Run
  runs/{run_id}/steps/{seq}        Step       (live trace, ordered by seq)
  runs/{run_id}/findings/{clause}  Finding    (one per policy clause)
  runs/{run_id}/audit/{seq}        AuditEvent (append-only)
  samples/{run_id}                 copy of a saved run, publicly readable
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

SCHEMA_VERSION = "v0"


class RunStatus(StrEnum):
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class StepKind(StrEnum):
    PLAN = "plan"
    SEARCH = "search"
    READ = "read"
    DRAFT = "draft"
    MONITOR = "monitor"
    REVISE = "revise"
    FINAL = "final"


class MonitorAction(StrEnum):
    CONTINUE = "continue"
    REVISE = "revise"
    STOP = "stop"


class CheckName(StrEnum):
    PARAGRAPH_EXISTS = "paragraph_exists"
    QUOTE_MATCHES = "quote_matches"
    IN_FORCE = "in_force"
    SUPPORTED = "supported"
    NO_INJECTION = "no_injection"
    ON_TASK = "on_task"


class FindingLabel(StrEnum):
    """One label per clause. LABEL_DEFINITIONS is the rule for choosing it, for the agent and the gold set."""

    OUTDATED_REFERENCE = "outdated_reference"
    GAP = "gap"
    CONFLICT = "conflict"
    COMPLIANT = "compliant"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


# Gap and conflict are both "substantive" findings (PLAN §4) and share one chip in the UI; this is the line
# between them. A clause that both omits something and contradicts the rule is a conflict.
LABEL_DEFINITIONS: dict[FindingLabel, str] = {
    FindingLabel.OUTDATED_REFERENCE: (
        "The clause's substance agrees with the rule in force, but it cites an instrument or paragraph that "
        "has been withdrawn or replaced. A stale citation alone is never a gap or a conflict."
    ),
    FindingLabel.GAP: (
        "The clause deals with the topic but leaves out something the paragraph in force requires, and "
        "nothing it says goes against the rule: the policy is silent where the rule demands an element."
    ),
    FindingLabel.CONFLICT: (
        "The clause permits, requires or describes a practice the paragraph in force forbids, or sets a "
        "figure or limit outside the one the rule allows: following the policy would breach the rule."
    ),
    FindingLabel.COMPLIANT: (
        "The clause meets the paragraph in force and cites nothing outdated or unverifiable."
    ),
    FindingLabel.INSUFFICIENT_EVIDENCE: (
        "The clause cannot be judged from the library: its own citation does not exist or cannot be "
        "verified, or the evidence does not settle it. It goes to human review."
    ),
}


class MonitorStatus(StrEnum):
    PASSED = "passed"
    REVISED = "revised"
    NEEDS_REVIEW = "needs_review"


class SignoffDecision(StrEnum):
    ACCEPT = "accept"
    REJECT = "reject"
    EDIT = "edit"


class Citation(BaseModel):
    instrument: str  # e.g. "NBFC Credit Facilities Directions, 2025"
    instrument_ref: str  # e.g. "RBI/DOR/2025-26/347"
    para: str  # official paragraph number, e.g. "10(4)"
    url: str  # RBI page URL (no paragraph anchors exist)
    quote: str  # verbatim text from the snapshot
    snapshot_date: str  # date the snapshot was taken, ISO
    content_hash: str  # sha256 of the paragraph text in the snapshot
    in_force: bool


class Check(BaseModel):
    name: CheckName
    passed: bool
    detail: str


class Verdict(BaseModel):
    action: MonitorAction
    checks: list[Check]
    reason: str


class Step(BaseModel):
    seq: int
    clause_id: str | None
    kind: StepKind
    summary: str  # one line shown in the trace
    tool: str | None = None
    verdict: Verdict | None = None
    latency_ms: int | None = None
    at: datetime


class Signoff(BaseModel):
    decision: SignoffDecision
    by: str
    at: datetime
    note: str = ""


class Finding(BaseModel):
    clause_id: str
    clause_text: str
    label: FindingLabel
    rationale: str
    citation: Citation | None
    replaced_citation: Citation | None = None  # what the policy cited, when outdated
    monitor_status: MonitorStatus
    signoff: Signoff | None = None


class AuditEvent(BaseModel):
    seq: int
    at: datetime
    actor: str  # "agent" | "monitor" | "verifier" | "officer:<uid>"
    event: str
    detail: str = ""


class RunSummary(BaseModel):
    clauses: int = 0
    outdated_reference: int = 0
    gap: int = 0
    conflict: int = 0
    compliant: int = 0
    insufficient_evidence: int = 0
    monitor_revisions: int = 0
    monitor_stops: int = 0


class Run(BaseModel):
    schema_version: str = SCHEMA_VERSION
    run_id: str
    owner_uid: str
    policy_id: str
    policy_title: str
    saved: bool = False  # True for labelled saved sample runs
    status: RunStatus
    error: str | None = None
    started_at: datetime
    finished_at: datetime | None = None
    agent_model: str
    monitor_model: str
    corpus_as_on: str
    summary: RunSummary = Field(default_factory=RunSummary)
    latency_ms: int | None = None
    cost_usd_estimate: float | None = None
