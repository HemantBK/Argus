"""Data contract between the API, Firestore and the web app (schema v0.1).

Firestore layout (written only by the API, see runs.py; documents are model_dump(mode="json"), so every
datetime is an ISO 8601 string):
  runs/{run_id}                    Run
  runs/{run_id}/steps/{seq}        Step       (live trace; doc id is seq zero-padded to 4 digits, "0001")
  runs/{run_id}/findings/{clause}  Finding    (one per policy clause, failed clauses included)
  runs/{run_id}/audit/{seq}        AuditEvent (append-only; doc id zero-padded like steps)
  samples/{run_id}                 copy of a saved run (same subcollections), publicly readable
  config/limits                    Limits     (kill switch and daily run cap; server-side only)
  config/custom_limits             CustomLimits (switch and daily cap for pasted clauses; server-side only)

A run of a clause the user pasted (Run.source "custom", see custom.py) carries expires_at, a Firestore
timestamp runs.CUSTOM_TTL (24 h) after it started, on the run and on every steps/findings/audit document,
so the Firestore TTL policy on expires_at (admin.py ttl) deletes all of it, normally within a day of expiry;
purge_custom.py deletes at once whatever has expired and TTL has not removed yet. Runs of the sample policies
have neither field.

v0.1 (D-34) added the optional fields Run.clauses, Citation.withdrawn_by / withdrawn_on,
AuditEvent.clause_id / model and Signoff.edited_rationale; also Finding.error and RunSummary.failed, so a
clause whose review failed is an explicit state rather than a missing document.

Later additive fields, each optional with a default so documents written before them still validate:
Citation.updated_as_on; Run.provider, Run.seeded and Run.rbi_as_on; Run.source and Run.expires_at;
Finding.agent_model, Finding.fallback_reason and RunSummary.fallback_clauses (a clause the fallback agent
model reviewed because the run's agent model was busy, see runs.py). New
document: config/custom_limits (CustomLimits). EvalResults (with EvalCondition,
EvalMetric, EvalCell, EvalCorpus) is the shape of eval_results/latest that the Evaluation page reads.

Dates: snapshot_date and corpus_as_on are the date the RBI pages were fetched. updated_as_on and rbi_as_on are
RBI's own "updated as on" date for the instrument in force; withdrawn_on / withdrawn_by say when and by what
a withdrawn instrument stopped being in force.
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StringConstraints, model_validator

SCHEMA_VERSION = "v0.1"


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
        "nothing it says goes against the rule: the policy is silent where the rule demands an element. An "
        "exception or permission the rule allows is not a required element: a clause stricter than the rule "
        "is not a gap."
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
    withdrawn_by: str | None = None  # instrument_ref of the instrument that withdrew it (corpus withdrawn_by)
    withdrawn_on: str | None = None  # ISO date the instrument stopped being in force (corpus valid_to)
    # ISO date of RBI's "updated as on" line for an instrument in force; None when withdrawn.
    updated_as_on: str | None = None


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
    edited_rationale: str | None = None  # the officer's rationale, for decision "edit"; the agent's is kept


class Finding(BaseModel):
    clause_id: str
    clause_text: str
    label: FindingLabel
    rationale: str
    citation: Citation | None
    replaced_citation: Citation | None = None  # what the policy cited, when outdated
    monitor_status: MonitorStatus
    signoff: Signoff | None = None
    # Set when the review of this clause failed (provider error, timeout, run budget): the label is then
    # insufficient_evidence, the citation None, and the clause goes to a person.
    error: str | None = None
    # The agent model whose draft produced this finding: Run.agent_model, or the fallback model when that
    # one was busy. None on a failed clause, and on findings written before this field.
    agent_model: str | None = None
    # Why the fallback model reviewed this clause, in plain English ("Gemini 3.8 Flash was busy (HTTP 429)
    # after 5 tries"); None when the run's agent model did.
    fallback_reason: str | None = None


class AuditEvent(BaseModel):
    seq: int
    at: datetime
    actor: str  # "agent" | "monitor" | "verifier" | "officer:<uid>"
    event: str
    detail: str = ""
    clause_id: str | None = None
    model: str | None = None  # the model behind an AI event (E2)


class RunSummary(BaseModel):
    clauses: int = 0
    outdated_reference: int = 0
    gap: int = 0
    conflict: int = 0
    compliant: int = 0
    insufficient_evidence: int = 0
    monitor_revisions: int = 0
    monitor_stops: int = 0
    failed: int = 0  # clauses whose review failed; the label counts above cover only reviewed clauses
    fallback_clauses: int = 0  # reviewed clauses whose finding came from the fallback agent model


class RunClause(BaseModel):
    clause_id: str
    title: str
    text: str


class Limits(BaseModel):
    """config/limits, read before every model-spending request (a run, or the smoke check).

    enabled false: nothing starts (503). Otherwise a request is refused (429) when today's (IST) count is at
    daily_run_cap, this hour's at hourly_run_cap (so one visitor cannot drain the day in minutes), or the
    signed-in user's own count today at per_user_daily_run_cap.

    Pasted-clause runs have their own switch and cap in config/custom_limits (CustomLimits), never here:
    every deployed revision validates this document with extra="forbid" and refuses all runs (503) when it
    holds a field the revision does not know, so a new field here would stop a rollback or an older revision.
    """

    model_config = ConfigDict(extra="forbid")

    daily_run_cap: StrictInt = Field(ge=0)
    hourly_run_cap: StrictInt = Field(ge=0)
    per_user_daily_run_cap: StrictInt = Field(ge=0)
    enabled: StrictBool


class CustomLimits(BaseModel):
    """config/custom_limits, read before every pasted-clause run (runs.read_custom_limits).

    A pasted-clause run starts only when this document exists, is well formed and has custom_enabled true;
    otherwise it is refused with 503 and sample runs are unaffected. It also counts against custom_daily_cap
    (today, IST) on top of every Limits cap. It is a separate document from config/limits so that revisions
    that predate pasted clauses never read it: writing it, switching it off or deleting it is safe whichever
    revision is serving.
    """

    model_config = ConfigDict(extra="forbid")

    custom_enabled: StrictBool
    custom_daily_cap: StrictInt = Field(ge=0)


RunSource = Literal["custom"]
CUSTOM_SOURCE: RunSource = "custom"


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
    corpus_as_on: str  # date the RBI pages were fetched (the library snapshot), ISO
    clauses: list[RunClause] = Field(default_factory=list)
    summary: RunSummary = Field(default_factory=RunSummary)
    latency_ms: int | None = None
    cost_usd_estimate: float | None = None
    provider: str | None = None  # LLM provider of the run (config.Settings.provider): vertex in the cloud
    seeded: bool = False  # True for runs written by a seed script with made-up content, never a real review
    rbi_as_on: str | None = None  # RBI's "updated as on" date of the instrument in force at the run, ISO
    # A pasted clause: "custom". None for a sample policy (the field is then left out of the document).
    source: RunSource | None = None
    # When Firestore TTL deletes the run and its documents; set exactly when source is "custom".
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def _custom_runs_expire(self) -> Self:
        if (self.source == CUSTOM_SOURCE) != (self.expires_at is not None):
            raise ValueError("expires_at is set on a custom run and only on a custom run")
        return self


# ---- eval_results/latest: the Evaluation page (web/src/eval/evalResults.ts) ------------------------------

MetricUnit = Literal["ratio", "seconds", "usd", "count"]
NonBlank = Annotated[str, StringConstraints(pattern=r"\S")]  # what the page's parser accepts as a string


class _EvalModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvalCondition(_EvalModel):
    id: NonBlank
    label: NonBlank


class EvalMetric(_EvalModel):
    id: NonBlank
    label: NonBlank
    unit: MetricUnit  # ratio is 0..1 (shown as a percentage)


class EvalCell(_EvalModel):
    metric: NonBlank
    condition: NonBlank
    value: float | None = Field(default=None, ge=0)  # None: not measured yet
    n_label: NonBlank | None = None  # sample size in words; required with a value

    @model_validator(mode="after")
    def _value_has_sample_size(self) -> Self:
        if self.value is not None and self.n_label is None:
            raise ValueError(f"{self.metric}/{self.condition}: a measured value must say its sample size")
        return self


class EvalCorpusDocument(_EvalModel):
    instrument: NonBlank
    instrument_ref: NonBlank


class EvalCorpus(_EvalModel):
    documents: list[EvalCorpusDocument]
    as_on: NonBlank
    paragraphs: int = Field(ge=0)
    supersession_rows: int = Field(ge=0)


class EvalResults(_EvalModel):
    run_date: NonBlank  # ISO date the evaluation ran
    conditions: list[EvalCondition]
    metrics: list[EvalMetric]
    results: list[EvalCell]
    corpus: EvalCorpus | None = None
    failure_cases: list[NonBlank] = Field(default_factory=list)  # plain-English sentences

    @model_validator(mode="after")
    def _cells_name_known_rows(self) -> Self:
        metrics = {m.id for m in self.metrics}
        conditions = {c.id for c in self.conditions}
        for cell in self.results:
            if cell.metric not in metrics:
                raise ValueError(f"result metric {cell.metric!r} is not in metrics")
            if cell.condition not in conditions:
                raise ValueError(f"result condition {cell.condition!r} is not in conditions")
        return self
