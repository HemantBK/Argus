"""The step monitor's verdicts: on every paragraph the agent reads, and on each drafted finding.

monitor_read checks one read step with the deterministic verifier alone (no model call): may the paragraph
the agent just read back a finding? It must exist in the snapshot and be in force. If not, the verdict is
REVISE and its reason (what is wrong, the paragraphs that do exist, where a replaced rule now sits) goes back
to the agent before its next step, so the course correction on a stale or made-up reference happens mid-run,
where the reference first enters the review. A withdrawn paragraph may still be quoted as what the policy
itself cites; the REVISE is about what the finding rests on.

monitor_finding runs the deterministic checks (citation, injection prescan), then one Gemini call for the
judgement calls (support, injection, on task), and applies fixed action rules:

  compliant label with an injection present        -> STOP (always)
  any deterministic citation check failed          -> REVISE
  an injection found (prescan or model)            -> STOP
  not supported, or off task                       -> REVISE
  otherwise                                        -> CONTINUE

On a STOP the draft's label is withheld (MonitorResult.label_withheld) when the draft says compliant or the
model finds that it followed the injected instruction: an attack that made the agent say "compliant" must
never land as the finding's label. The caller then records insufficient evidence, quoting the draft.

When the policy clause itself cites something the library cannot back (a paragraph that does not exist, a
withdrawn instrument, an unknown instrument), the verdict's reason says so, whatever the action: that is the
deterministic verifier's catch, and it must be visible in the trace even when the draft already handles it.

Failures of the model call are not swallowed: LLMError from the client (including a reply that breaks the
schema) and MonitorError for a judgement that still does not fit the typed model both propagate, and the
caller records the step as failed.
"""

import hashlib
import logging
import time
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, StrictBool, ValidationError

from argus_api.corpus.common import normalise
from argus_api.corpus.store import Corpus
from argus_api.llm import LLM, estimate_cost_usd
from argus_api.logs import log_event
from argus_api.monitor.checks import check_citation, check_reference, prescan_injection
from argus_api.monitor.prompts import MONITOR_PROMPT_VERSION, MONITOR_RESPONSE_SCHEMA, build_monitor_prompt
from argus_api.schema import Check, CheckName, FindingLabel, MonitorAction, Verdict

log = logging.getLogger("argus.monitor")

CITATION_CHECKS = (CheckName.PARAGRAPH_EXISTS, CheckName.QUOTE_MATCHES, CheckName.IN_FORCE)


class MonitorError(RuntimeError):
    pass


@dataclass(frozen=True)
class MonitorResult:
    verdict: Verdict
    label_withheld: bool  # STOP on a draft whose label must not be used (see the module docstring)


class DraftFinding(BaseModel):
    """What the agent proposes for one clause, before the monitor has seen it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    clause_id: str
    label: FindingLabel
    rationale: str
    citation_instrument_ref: str  # the RBI rule the finding rests on (must be in force)
    citation_para: str
    citation_quote: str
    policy_cited_instrument_ref: str | None = None  # what the policy clause itself cites, if anything
    policy_cited_para: str | None = None


class MonitorJudgement(BaseModel):
    """The model's answer, shaped by prompts.MONITOR_RESPONSE_SCHEMA."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    supported: StrictBool
    supported_detail: str
    injection_present: StrictBool
    injection_quote: str
    agent_followed_injection: StrictBool
    injection_detail: str
    on_task: StrictBool
    on_task_detail: str


PolicyCitationStatus = Literal["none", "unknown_instrument", "missing_paragraph", "withdrawn", "current"]


def policy_citation_status(corpus: Corpus, draft: DraftFinding) -> tuple[PolicyCitationStatus, str]:
    """Classify the reference the policy clause itself cites, with a plain-English description."""
    ref, para = draft.policy_cited_instrument_ref, draft.policy_cited_para
    if ref is None:
        if para is not None:
            raise MonitorError(
                f"draft for {draft.clause_id} gives a policy paragraph {para!r} without an instrument"
            )
        return "none", "The policy clause cites no RBI instrument."
    where = f"paragraph {para} of {ref}" if para is not None else ref
    if ref not in corpus.instruments:
        return "unknown_instrument", f"The policy clause cites {where}; {ref} is not in the RBI library."
    inst = corpus.instrument(ref)
    if not inst.in_force:
        current = corpus.current_instrument(ref)
        return (
            "withdrawn",
            f"The policy clause cites {where}; {inst.instrument} is no longer in force and has been replaced "
            f"by {current.instrument} ({current.instrument_ref}).",
        )
    if para is not None and corpus.paragraph(ref, para) is None:
        return (
            "missing_paragraph",
            f"The policy clause cites {where}, which does not exist in the RBI snapshot of "
            f"{inst.snapshot_date.isoformat()}.",
        )
    return "current", f"The policy clause cites {where}, which is in the library and in force."


def _label_consistency(draft: DraftFinding, status: PolicyCitationStatus, facts: str) -> str | None:
    """Why the label cannot stand given the policy's own citation, or None when it can (LABEL_DEFINITIONS)."""
    if draft.label == FindingLabel.COMPLIANT and status in (
        "unknown_instrument",
        "missing_paragraph",
        "withdrawn",
    ):
        return f"{facts} A clause that cites something outdated or unverifiable cannot be labelled compliant."
    if draft.label == FindingLabel.OUTDATED_REFERENCE and status != "withdrawn":
        return (
            f"{facts} An outdated reference needs the policy to cite a withdrawn or replaced instrument, "
            "so this label does not fit."
        )
    return None


def _contains(haystack: str, needle: str) -> bool:
    return bool(needle.strip()) and normalise(needle) in normalise(haystack)


def _injection_check(prescan: Check, j: MonitorJudgement, clause_text: str) -> Check:
    parts: list[str] = []
    if not prescan.passed:
        parts.append(prescan.detail)
    elif j.injection_present:
        if _contains(clause_text, j.injection_quote):
            parts.append(
                f'The monitor found text in the policy addressed to the reviewer: "{j.injection_quote}". '
                f"{j.injection_detail}"
            )
        else:
            parts.append(
                "The monitor reported text in the policy addressed to the reviewer, but its quote does not "
                f"appear in the clause, so a person must check: {j.injection_detail}"
            )
    if j.agent_followed_injection:
        parts.append(f"The draft relies on an instruction found in the policy: {j.injection_detail}")
    if parts:
        return Check(name=CheckName.NO_INJECTION, passed=False, detail=" ".join(parts))
    return Check(
        name=CheckName.NO_INJECTION,
        passed=True,
        detail="No instruction aimed at the reviewer was found in the policy text, and the draft follows "
        "none.",
    )


def _judge(llm: LLM, model: str, prompt: str, clause_id: str, run_id: str | None) -> MonitorJudgement:
    started = time.perf_counter()
    raw, completion = llm.complete_json(model, prompt, MONITOR_RESPONSE_SCHEMA)
    latency_ms = round((time.perf_counter() - started) * 1000)
    log_event(
        log,
        "monitor_call",
        run_id=run_id,
        clause_id=clause_id,
        model=model,
        served_model=completion.model,
        prompt_version=MONITOR_PROMPT_VERSION,
        prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        prompt_chars=len(prompt),
        output_chars=len(completion.text),
        input_tokens=completion.input_tokens,
        output_tokens=completion.output_tokens,
        latency_ms=latency_ms,
        cost_usd_estimate=estimate_cost_usd(model, completion.input_tokens, completion.output_tokens),
    )
    try:
        return MonitorJudgement.model_validate(raw)
    except ValidationError as e:
        raise MonitorError(
            f"monitor model {model} returned a judgement that does not fit the schema: {e}"
        ) from e


def monitor_read(
    corpus: Corpus, instrument_ref: str, para: str, *, clause_id: str, run_id: str | None = None
) -> Verdict:
    """The verdict on one read step: CONTINUE when the paragraph can back a finding, else REVISE."""
    checks = check_reference(corpus, instrument_ref, para)
    failed = [c for c in checks if not c.passed]
    where = f"paragraph {para} of {instrument_ref}"
    if failed:
        action = MonitorAction.REVISE
        reason = f"Revise the plan: the finding cannot rest on {where}. " + " ".join(c.detail for c in failed)
        if corpus.paragraph(instrument_ref, para) is not None:  # it exists but is not in force
            reason += " It may appear only as what the policy itself cites."
    else:
        action = MonitorAction.CONTINUE
        reason = (
            f"{where[0].upper()}{where[1:]} exists in the RBI snapshot and is in force, so a finding may "
            "rest on it."
        )
    log_event(
        log,
        "monitor_verdict",
        run_id=run_id,
        clause_id=clause_id,
        step="read",
        model=None,
        action=action.value,
        failed_checks=[c.name.value for c in failed],
        instrument_ref=instrument_ref,
        para=para,
    )
    return Verdict(action=action, checks=checks, reason=reason)


def label_withheld(action: MonitorAction, label: FindingLabel, agent_followed_injection: bool) -> bool:
    """True when a STOP must not keep the draft's label: it says compliant, or it obeys the injection."""
    return action == MonitorAction.STOP and (label == FindingLabel.COMPLIANT or agent_followed_injection)


def monitor_finding(
    llm: LLM,
    model: str,
    corpus: Corpus,
    clause_text: str,
    draft: DraftFinding,
    evidence_text: str,
    *,
    run_id: str | None = None,
) -> MonitorResult:
    """Check one drafted finding and decide continue / revise / stop. One model call, always."""
    citation = check_citation(
        corpus, draft.citation_instrument_ref, draft.citation_para, draft.citation_quote
    )
    prescan = prescan_injection(clause_text)
    status, facts = policy_citation_status(corpus, draft)

    prompt = build_monitor_prompt(
        clause_text=clause_text,
        label=draft.label.value,
        rationale=draft.rationale,
        citation_instrument_ref=draft.citation_instrument_ref,
        citation_para=draft.citation_para,
        citation_quote=draft.citation_quote,
        evidence_text=evidence_text,
        policy_citation_facts=facts,
    )
    j = _judge(llm, model, prompt, draft.clause_id, run_id)

    mislabel = _label_consistency(draft, status, facts)
    if mislabel is not None:
        supported = Check(name=CheckName.SUPPORTED, passed=False, detail=mislabel)
    else:
        supported = Check(name=CheckName.SUPPORTED, passed=j.supported, detail=j.supported_detail)
    injection = _injection_check(prescan, j, clause_text)
    on_task = Check(name=CheckName.ON_TASK, passed=j.on_task, detail=j.on_task_detail)
    checks = [*citation, supported, injection, on_task]

    action, reason = decide(draft.label, checks)
    if status in ("unknown_instrument", "missing_paragraph", "withdrawn") and facts not in reason:
        reason += f" The verifier also checked the policy's own citation: {facts}"
    withheld = label_withheld(action, draft.label, j.agent_followed_injection)
    log_event(
        log,
        "monitor_verdict",
        run_id=run_id,
        clause_id=draft.clause_id,
        step="draft",
        model=model,
        action=action.value,
        failed_checks=[c.name.value for c in checks if not c.passed],
        label_withheld=withheld,
    )
    return MonitorResult(Verdict(action=action, checks=checks, reason=reason), withheld)


def decide(label: FindingLabel, checks: list[Check]) -> tuple[MonitorAction, str]:
    """The action rules. `checks` must hold exactly one check per CheckName."""
    by_name = {c.name: c for c in checks}
    if len(by_name) != len(checks) or set(by_name) != set(CheckName):
        raise MonitorError(f"decide needs one check per name, got {[c.name.value for c in checks]}")
    injection = by_name[CheckName.NO_INJECTION]
    citation_failures = [by_name[n] for n in CITATION_CHECKS if not by_name[n].passed]
    judgement_failures = [
        by_name[n] for n in (CheckName.SUPPORTED, CheckName.ON_TASK) if not by_name[n].passed
    ]

    if not injection.passed and label == FindingLabel.COMPLIANT:
        return MonitorAction.STOP, (
            f"Stopped for human review. {injection.detail} The draft labels this clause compliant, and a "
            "claim written into the policy can never establish compliance."
        )
    if citation_failures:
        reason = "Revise the finding. " + " ".join(c.detail for c in citation_failures)
        if not injection.passed:
            reason += f" Also: {injection.detail}"
        return MonitorAction.REVISE, reason
    if not injection.passed:
        return MonitorAction.STOP, f"Stopped for human review. {injection.detail}"
    if judgement_failures:
        return MonitorAction.REVISE, "Revise the finding. " + " ".join(c.detail for c in judgement_failures)
    return MonitorAction.CONTINUE, (
        "All checks passed: the cited paragraph is in the RBI library, the quote matches it word for word, "
        "the instrument is in force, and the quoted rule supports the finding."
    )
