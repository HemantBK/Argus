"""What the runner records for one case under one condition and repeat; the input of every metric."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from argus_api.schema import CheckName, FindingLabel, MonitorAction, MonitorStatus

Condition = Literal["plain_gemini", "agent_no_monitor", "full_argus", "monitor_only"]
CONDITIONS: tuple[Condition, ...] = ("plain_gemini", "agent_no_monitor", "full_argus", "monitor_only")
# Conditions run over the clause and attack cases; monitor_only runs over the bad drafts.
CASE_CONDITIONS: tuple[Condition, ...] = ("plain_gemini", "agent_no_monitor", "full_argus")
INTERVENTIONS = (MonitorAction.REVISE, MonitorAction.STOP)


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CitationOut(_Frozen):
    instrument_ref: str
    para: str
    quote: str


class ReferenceOut(_Frozen):
    instrument_ref: str
    para: str | None


class CitationAssessment(_Frozen):
    """The predicted citation checked against the corpus and the gold citation."""

    exists: bool  # the paragraph is in the corpus
    in_force: bool  # its instrument is in force
    quote_verified: bool  # Corpus.verify_quote matches
    same_para_as_gold: bool  # same instrument_ref and para as the gold citation

    @property
    def accurate(self) -> bool:
        return self.exists and self.in_force and self.quote_verified and self.same_para_as_gold


class StepRecord(_Frozen):
    index: int  # 1-based position in the clause's trace
    kind: str  # schema.StepKind value, or "monitor" for a monitor_only verdict
    action: MonitorAction | None  # the monitor's verdict on this step, if it has one


class CaseRecord(_Frozen):
    condition: Condition
    case_id: str
    repeat: int  # 0-based
    kind: Literal["clause", "attack", "bad_draft"]
    attack_type: str | None = None
    # gold
    gold_label: FindingLabel | None = None  # None for bad drafts
    has_gold_citation: bool = False
    gold_stale: bool = False  # the policy cites a withdrawn or replaced instrument (gold_replaced_citation)
    gold_replaced_instrument_ref: str | None = None
    expected_monitor_action: MonitorAction | None = None
    expected_failed_check: CheckName | None = None
    # prediction
    predicted_label: FindingLabel | None = None  # None when the review failed (see error)
    predicted_citation: CitationOut | None = None
    predicted_replaced: ReferenceOut | None = None
    citation_assessment: CitationAssessment | None = None  # None: no gold or no predicted citation
    rationale: str | None = None
    monitor_active: bool = False
    monitor_status: MonitorStatus | None = None
    steps: list[StepRecord] = []
    monitor_actions: list[MonitorAction] = []
    final_action: MonitorAction | None = None  # monitor_only: the verdict on the draft
    failed_checks: list[CheckName] = []  # monitor_only: checks that failed
    first_intervention_step: int | None = None  # StepRecord.index of the first REVISE or STOP
    first_intervention_action: MonitorAction | None = None
    error: str | None = None
    # cost
    latency_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    models: list[str] = []

    @property
    def stopped(self) -> bool:
        return MonitorAction.STOP in self.monitor_actions

    @property
    def label_correct(self) -> bool:
        return (
            self.error is None
            and self.predicted_label is not None
            and self.predicted_label == self.gold_label
        )

    @property
    def flagged_for_review(self) -> bool:
        """The finding reaches the officer marked for a person: insufficient evidence or needs review."""
        return (
            self.predicted_label == FindingLabel.INSUFFICIENT_EVIDENCE
            or self.monitor_status == MonitorStatus.NEEDS_REVIEW
            or self.error is not None
        )


def first_intervention(steps: list[StepRecord]) -> tuple[int | None, MonitorAction | None]:
    for s in steps:
        if s.action in INTERVENTIONS:
            return s.index, s.action
    return None, None
