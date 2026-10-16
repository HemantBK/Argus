"""The review loop for one policy clause.

  plan      the review starts (no model call)
  search /  Gemini chat with the three library tools, at most MAX_TOOL_CALLS tool calls in total
  read
  monitor   after every read: monitor.monitor_read, the deterministic verifier on the paragraph just read
            (exists, in force; no model call). On REVISE its reason goes back to the agent as a message
            before the agent's next turn, and into the research notes the draft is written from
  draft     one structured-output call: the finding as a DraftFinding
  monitor   monitor.monitor_finding (deterministic checks + one Gemini judgement)
  revise    on REVISE of the draft only: one revision call given the monitor's reason, then monitor again
  final     the Finding, with a Citation built from the corpus, never from model text

Outcome rules:
  CONTINUE on the draft, no REVISE     -> monitor_status passed
  at any step
  CONTINUE on the draft after a REVISE -> monitor_status revised (the monitor redirected a read, or the one
  (of a read, or of the first draft)      revision of the draft fixed what it objected to)
  STOP at any point                    -> monitor_status needs_review; the rationale keeps the monitor's
                                          reason, which quotes the injected text. The label stands only
                                          when the monitor does not withhold it: a draft that says
                                          compliant, or that follows the injected instruction, becomes
                                          insufficient_evidence with the agent's draft quoted
  REVISE again after the revision      -> needs_review, label insufficient_evidence (the agent's label was
                                          not established), the agent's draft kept in the rationale
  final citation not verifiable        -> needs_review, label insufficient_evidence, citation None (never
                                          invented)

Every step is passed to `emit` as it happens, as an AgentStep (a schema.Step with the model, tokens and cost
estimate of the model call the step made). Summaries are one line of plain English describing what was done;
no model reasoning text is shown. Failures (LLMError, MonitorError, CorpusError, an unusable draft) are
raised as ClauseReviewError after a final step saying the review failed, for the caller to save as a failed
clause.
"""

import hashlib
import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from argus_api.agent.prompts import (
    AGENT_PROMPT_VERSION,
    DRAFT_RESPONSE_SCHEMA,
    clause_message,
    corpus_as_on,
    draft_prompt,
    monitor_note,
    revision_prompt,
    system_prompt,
)
from argus_api.agent.tools import TOOL_SEARCH, TOOL_SPECS, ToolResult, library_index, run_tool
from argus_api.config import Settings
from argus_api.corpus.common import normalise
from argus_api.corpus.store import Corpus, CorpusError, quote_in_text
from argus_api.llm import LLM, Completion, LLMError, Message, ToolSpec, Turn, estimate_cost_usd
from argus_api.logs import log_event
from argus_api.monitor import DraftFinding, MonitorError, MonitorResult, monitor_finding, monitor_read
from argus_api.monitor.core import CITATION_CHECKS
from argus_api.schema import (
    Citation,
    Finding,
    FindingLabel,
    MonitorAction,
    MonitorStatus,
    Step,
    StepKind,
    Verdict,
)

log = logging.getLogger("argus.agent")

MAX_TOOL_CALLS = 6
IST = timezone(timedelta(hours=5, minutes=30))

LABEL_WORDS: dict[FindingLabel, str] = {
    FindingLabel.OUTDATED_REFERENCE: "outdated reference",
    FindingLabel.GAP: "gap",
    FindingLabel.CONFLICT: "conflict",
    FindingLabel.COMPLIANT: "compliant",
    FindingLabel.INSUFFICIENT_EVIDENCE: "insufficient evidence",
}
CHECK_WORDS = {
    "paragraph_exists": "paragraph exists",
    "quote_matches": "quote matches",
    "in_force": "in force",
    "supported": "supported",
    "no_injection": "no injection",
    "on_task": "on task",
}


class Clause(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    text: str


class AgentStep(Step):
    """A trace step plus the cost of the model call it made (None fields: no call, or count unknown)."""

    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd_estimate: float | None = None


Emit = Callable[[AgentStep], None]


class AgentError(RuntimeError):
    """The agent produced something the loop cannot use (for example a draft that is not a DraftFinding)."""


class ClauseReviewError(RuntimeError):
    """The review of one clause failed; the caller saves the clause as failed. The cause is chained."""

    def __init__(self, clause_id: str, stage: StepKind, message: str) -> None:
        super().__init__(f"review of clause {clause_id} failed at the {stage.value} step: {message}")
        self.clause_id = clause_id
        self.stage = stage


class SeqCounter:
    """Thread-safe step numbering. Share one across the clauses of a run so step ids never collide."""

    def __init__(self, start: int = 1) -> None:
        self._next = start
        self._lock = threading.Lock()

    def __call__(self) -> int:
        with self._lock:
            n = self._next
            self._next += 1
            return n


@dataclass(frozen=True)
class ReviewOutcome:
    finding: Finding
    verdicts: list[Verdict]  # one per monitor step (read checks, then draft checks), in order
    revised: bool  # the monitor asked for a revision at some step (a read or the draft)
    stopped: bool
    tool_calls: int
    input_tokens: int | None  # totals over every model call; None if any call reported no count
    output_tokens: int | None
    cost_usd_estimate: float | None
    latency_ms: int


@dataclass(frozen=True)
class _Usage:
    model: str
    input_tokens: int | None
    output_tokens: int | None
    cost_usd_estimate: float | None


class _UsageRecorder:
    """Wraps an LLM and keeps the Completion of every complete_json call (the monitor's usage)."""

    def __init__(self, inner: LLM) -> None:
        self._inner = inner
        self.completions: list[Completion] = []

    def complete(self, model: str, prompt: str) -> Completion:
        return self._inner.complete(model, prompt)

    def complete_json(
        self, model: str, prompt: str, schema: dict[str, Any]
    ) -> tuple[dict[str, Any], Completion]:
        value, completion = self._inner.complete_json(model, prompt, schema)
        self.completions.append(completion)
        return value, completion

    def chat(self, model: str, messages: list[Message], tools: list[ToolSpec]) -> Turn:
        return self._inner.chat(model, messages, tools)


def _ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _where(ref: str, para: str) -> str:
    return f"para {para} of {ref}"


class _Review:
    def __init__(
        self,
        llm: LLM,
        settings: Settings,
        corpus: Corpus,
        clause: Clause,
        emit: Emit,
        next_seq: Callable[[], int],
        today: date,
        run_id: str | None,
    ) -> None:
        self.llm = llm
        self.settings = settings
        self.corpus = corpus
        self.clause = clause
        self.emit_fn = emit
        self.next_seq = next_seq
        self.today = today
        self.run_id = run_id
        self.stage = StepKind.PLAN
        self.usage: list[_Usage] = []
        self.tool_calls = 0
        self.read_verdicts: list[Verdict] = []
        self.system = system_prompt(corpus, today, MAX_TOOL_CALLS)

    # ---- recording ----------------------------------------------------------------------------------

    def emit(
        self,
        kind: StepKind,
        summary: str,
        *,
        tool: str | None = None,
        verdict: Verdict | None = None,
        latency_ms: int | None = None,
        usage: _Usage | None = None,
    ) -> None:
        if "\n" in summary:
            raise AssertionError(f"step summary must be one line: {summary!r}")
        step = AgentStep(
            seq=self.next_seq(),
            clause_id=self.clause.id,
            kind=kind,
            summary=summary,
            tool=tool,
            verdict=verdict,
            latency_ms=latency_ms,
            at=datetime.now(UTC),
            model=usage.model if usage else None,
            input_tokens=usage.input_tokens if usage else None,
            output_tokens=usage.output_tokens if usage else None,
            cost_usd_estimate=usage.cost_usd_estimate if usage else None,
        )
        self.emit_fn(step)

    def record_call(
        self,
        call: str,
        model: str,
        served_model: str,
        prompt_sha256: str,
        prompt_chars: int,
        output_chars: int,
        input_tokens: int | None,
        output_tokens: int | None,
        latency_ms: int,
    ) -> _Usage:
        usage = _Usage(
            model, input_tokens, output_tokens, estimate_cost_usd(model, input_tokens, output_tokens)
        )
        self.usage.append(usage)
        log_event(
            log,
            "agent_call",
            run_id=self.run_id,
            clause_id=self.clause.id,
            call=call,
            model=model,
            served_model=served_model,
            thinking=self.settings.agent_thinking,
            prompt_version=AGENT_PROMPT_VERSION,
            prompt_sha256=prompt_sha256,
            prompt_chars=prompt_chars,
            output_chars=output_chars,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            cost_usd_estimate=usage.cost_usd_estimate,
        )
        return usage

    # ---- research: chat with tools ------------------------------------------------------------------

    def research(self) -> list[dict[str, Any]]:
        self.stage = StepKind.SEARCH
        model = self.settings.agent_model
        messages = [
            Message(role="system", content=self.system),
            Message(role="user", content=clause_message(self.clause.id, self.clause.text)),
        ]
        notes: list[dict[str, Any]] = []
        while self.tool_calls < MAX_TOOL_CALLS:
            turn = self.llm.chat(model, messages, list(TOOL_SPECS))
            transcript = json.dumps(
                [[m.role, m.content, [c.id for c in m.tool_calls], m.tool_call_id] for m in messages]
            )
            usage = self.record_call(
                "chat",
                model,
                turn.model,
                _sha256(transcript),
                len(transcript),
                len(turn.text) + len(json.dumps([[c.name, c.args] for c in turn.tool_calls])),
                turn.input_tokens,
                turn.output_tokens,
                turn.latency_ms,
            )
            if not turn.tool_calls:
                self.emit(
                    StepKind.PLAN,
                    f"Finished looking things up after {self.tool_calls} tool call(s); drafting the finding.",
                    latency_ms=turn.latency_ms,
                    usage=usage,
                )
                return notes
            messages.append(Message(role="assistant", content=turn.text, tool_calls=list(turn.tool_calls)))
            corrections: list[str] = []
            room = MAX_TOOL_CALLS - self.tool_calls
            allowed, skipped = turn.tool_calls[:room], turn.tool_calls[room:]
            for i, call in enumerate(allowed):
                started = time.perf_counter()
                result = run_tool(self.corpus, library_index(self.corpus), call.name, call.args)
                tool_ms = _ms(started)
                self.tool_calls += 1
                notes.append({"tool": call.name, "args": call.args, "result": result.output})
                messages.append(
                    Message(
                        role="tool",
                        content=json.dumps(result.output, ensure_ascii=False),
                        tool_call_id=call.id,
                    )
                )
                first = i == 0  # the model turn's own time and tokens go on its first tool step
                self.emit(
                    StepKind.SEARCH if call.name == TOOL_SEARCH else StepKind.READ,
                    result.summary,
                    tool=call.name,
                    latency_ms=tool_ms + (turn.latency_ms if first else 0),
                    usage=usage if first else None,
                )
                note = self.check_read(result)
                if note is not None:
                    corrections.append(note)
                    notes.append({"step_monitor": "revise", "reason": note})
            if corrections:
                # After the tool results, so the agent's next turn sees the monitor's verdicts too.
                messages.append(Message(role="user", content=monitor_note(corrections)))
            if skipped:
                names = ", ".join(c.name for c in skipped)
                self.emit(
                    StepKind.PLAN,
                    f"Tool-call limit of {MAX_TOOL_CALLS} reached: {len(skipped)} further call(s) not run "
                    f"({names}); drafting with what was found.",
                )
                return notes
        self.emit(
            StepKind.PLAN,
            f"Tool-call limit of {MAX_TOOL_CALLS} reached; drafting the finding with what was found.",
        )
        return notes

    def check_read(self, result: ToolResult) -> str | None:
        """The step monitor on one read: emit its verdict as a monitor step; return the reason on REVISE."""
        if result.reference is None:
            return None
        ref, para = result.reference
        self.stage = StepKind.MONITOR
        started = time.perf_counter()
        verdict = monitor_read(self.corpus, ref, para, clause_id=self.clause.id, run_id=self.run_id)
        self.read_verdicts.append(verdict)
        failed = [CHECK_WORDS[c.name.value] for c in verdict.checks if not c.passed]
        tail = f" (failed: {', '.join(failed)})" if failed else " (paragraph exists, in force)"
        self.emit(
            StepKind.MONITOR,
            f"Monitor verdict on the read of {_where(ref, para)}: {verdict.action.value}{tail}.",
            verdict=verdict,
            latency_ms=_ms(started),
        )
        self.stage = StepKind.SEARCH
        return verdict.reason if verdict.action == MonitorAction.REVISE else None

    # ---- draft, monitor, revise --------------------------------------------------------------------

    def to_draft(self, raw: dict[str, Any]) -> DraftFinding:
        fields = dict(raw)
        for key in ("policy_cited_instrument_ref", "policy_cited_para"):
            value = fields.get(key)
            fields[key] = value.strip() or None if isinstance(value, str) else value
        if fields.get("policy_cited_para") is not None and fields.get("policy_cited_instrument_ref") is None:
            raise AgentError(
                f"draft names policy paragraph {fields['policy_cited_para']!r} without the instrument it "
                "belongs to"
            )
        try:
            return DraftFinding(clause_id=self.clause.id, **fields)
        except ValidationError as e:
            raise AgentError(f"draft does not fit DraftFinding: {e}") from e

    def structured(self, call: str, prompt: str) -> tuple[DraftFinding, dict[str, Any], _Usage, int]:
        model = self.settings.agent_model
        started = time.perf_counter()
        raw, completion = self.llm.complete_json(model, prompt, DRAFT_RESPONSE_SCHEMA)
        latency = _ms(started)
        usage = self.record_call(
            call,
            model,
            completion.model,
            _sha256(prompt),
            len(prompt),
            len(completion.text),
            completion.input_tokens,
            completion.output_tokens,
            latency,
        )
        return self.to_draft(raw), raw, usage, latency

    def monitor(self, draft: DraftFinding) -> MonitorResult:
        self.stage = StepKind.MONITOR
        cited = self.corpus.paragraph(draft.citation_instrument_ref, draft.citation_para)
        recorder = _UsageRecorder(self.llm)
        model = self.settings.monitor_model
        started = time.perf_counter()
        result = monitor_finding(
            recorder,
            model,
            self.corpus,
            self.clause.text,
            draft,
            cited.text if cited is not None else "",
            run_id=self.run_id,
        )
        verdict = result.verdict
        latency = _ms(started)
        if len(recorder.completions) != 1:
            raise MonitorError(f"expected one monitor model call, saw {len(recorder.completions)}")
        c = recorder.completions[0]
        usage = _Usage(
            model, c.input_tokens, c.output_tokens, estimate_cost_usd(model, c.input_tokens, c.output_tokens)
        )
        self.usage.append(usage)
        failed = [CHECK_WORDS[c.name.value] for c in verdict.checks if not c.passed]
        tail = f" (failed: {', '.join(failed)})" if failed else " (all checks passed)"
        self.emit(
            StepKind.MONITOR,
            f"Monitor verdict on the draft: {verdict.action.value}{tail}.",
            verdict=verdict,
            latency_ms=latency,
            usage=usage,
        )
        return result

    def revision_context(self, draft: DraftFinding) -> str:
        """Library text that bears on a failed citation: the replacement rule, the parent paragraph, or the
        cited paragraph itself. Only corpus text; the model still has to choose and quote."""
        ref, para = draft.citation_instrument_ref, draft.citation_para
        if ref not in self.corpus.instruments:
            return ""
        found: list[tuple[str, str]] = []
        cited = self.corpus.paragraph(ref, para)
        if not self.corpus.in_force(ref):
            for row in self.corpus.rule_moves(ref, para):
                found.append((row.new_instrument_ref, row.new_para))
        elif cited is None:
            stem = para.split("(", 1)[0].split(".", 1)[0]
            if self.corpus.paragraph(ref, stem) is not None:
                found.append((ref, stem))
        else:
            found.append((ref, para))
        blocks = []
        for r, p in dict.fromkeys(found):
            paragraph = self.corpus.paragraph(r, p)
            if paragraph is None:
                raise CorpusError(
                    f"supersession or parent lookup gave {_where(r, p)}, which is not in the corpus"
                )
            state = "in force" if paragraph.in_force else "not in force"
            blocks.append(f"{_where(r, p)} ({state}):\n{paragraph.text}")
        return "\n\n".join(blocks)

    # ---- the finding ----------------------------------------------------------------------------------

    def citation(self, ref: str, para: str, quote: str) -> Citation:
        p = self.corpus.paragraph(ref, para)
        if p is None or not quote_in_text(quote, p.text):
            raise AgentError(f"citation of {_where(ref, para)} cannot be built from the corpus")
        return Citation(
            instrument=p.instrument,
            instrument_ref=p.instrument_ref,
            para=p.para,
            url=p.url,
            quote=normalise(quote),
            snapshot_date=p.snapshot_date.isoformat(),
            content_hash=p.content_hash,
            in_force=p.in_force,
        )

    def replaced_citation(self, draft: DraftFinding) -> Citation | None:
        """What the policy itself cites, for an outdated reference: the whole old paragraph, verbatim."""
        ref, para = draft.policy_cited_instrument_ref, draft.policy_cited_para
        if ref is None or para is None or self.corpus.paragraph(ref, para) is None:
            return None
        p = self.corpus.paragraph(ref, para)
        assert p is not None
        return self.citation(ref, para, normalise(p.text))

    def finding(self, draft: DraftFinding, result: MonitorResult, revised: bool) -> Finding:
        verdict = result.verdict
        checks = {c.name: c for c in verdict.checks}
        citation_failures = [checks[n] for n in CITATION_CHECKS if not checks[n].passed]
        if verdict.action == MonitorAction.CONTINUE:
            status = MonitorStatus.REVISED if revised else MonitorStatus.PASSED
        else:
            status = MonitorStatus.NEEDS_REVIEW
        stop_note = f" {verdict.reason}" if verdict.action == MonitorAction.STOP else ""
        if citation_failures:
            details = " ".join(c.detail for c in citation_failures)
            return Finding(
                clause_id=self.clause.id,
                clause_text=self.clause.text,
                label=FindingLabel.INSUFFICIENT_EVIDENCE,
                rationale=(
                    "Argus could not verify the RBI citation for this finding against the library, so a "
                    f"person must review this clause. {details} The agent's draft said "
                    f"{LABEL_WORDS[draft.label]}: {draft.rationale}{stop_note}"
                ),
                citation=None,
                monitor_status=MonitorStatus.NEEDS_REVIEW,
            )
        citation = self.citation(draft.citation_instrument_ref, draft.citation_para, draft.citation_quote)
        if result.label_withheld:
            # Stopped, and the draft's label is what the injected text asked for (or relies on it).
            return Finding(
                clause_id=self.clause.id,
                clause_text=self.clause.text,
                label=FindingLabel.INSUFFICIENT_EVIDENCE,
                rationale=(
                    "The step monitor stopped this finding for human review and Argus does not use the "
                    f"agent's label. {verdict.reason} The agent's draft said {LABEL_WORDS[draft.label]}: "
                    f"{draft.rationale}"
                ),
                citation=citation,
                monitor_status=MonitorStatus.NEEDS_REVIEW,
            )
        if verdict.action == MonitorAction.REVISE:
            # Still rejected after the one revision: the agent's label is not established.
            return Finding(
                clause_id=self.clause.id,
                clause_text=self.clause.text,
                label=FindingLabel.INSUFFICIENT_EVIDENCE,
                rationale=(
                    "The step monitor still objected after one revision, so a person must review this "
                    f"clause. {verdict.reason} The agent's draft said {LABEL_WORDS[draft.label]}: "
                    f"{draft.rationale}"
                ),
                citation=citation,
                monitor_status=MonitorStatus.NEEDS_REVIEW,
            )
        return Finding(
            clause_id=self.clause.id,
            clause_text=self.clause.text,
            label=draft.label,
            rationale=draft.rationale + stop_note,
            citation=citation,
            replaced_citation=(
                self.replaced_citation(draft) if draft.label == FindingLabel.OUTDATED_REFERENCE else None
            ),
            monitor_status=status,
        )

    # ---- the whole review --------------------------------------------------------------------------

    def run(self) -> tuple[Finding, list[Verdict], bool]:
        self.emit(
            StepKind.PLAN,
            f"Started the review of clause {self.clause.id} against the RBI library as on "
            f"{corpus_as_on(self.corpus).isoformat()}, with up to {MAX_TOOL_CALLS} tool calls.",
        )
        notes = self.research()

        self.stage = StepKind.DRAFT
        base = draft_prompt(self.system, self.clause.id, self.clause.text, notes, self.tool_calls)
        draft, raw, usage, latency = self.structured("draft", base)
        self.emit(
            StepKind.DRAFT,
            f"Drafted the finding: {LABEL_WORDS[draft.label]}, citing "
            f"{_where(draft.citation_instrument_ref, draft.citation_para)}.",
            latency_ms=latency,
            usage=usage,
        )
        results = [self.monitor(draft)]
        revised = False
        if results[-1].verdict.action == MonitorAction.REVISE:
            self.stage = StepKind.REVISE
            first = results[-1].verdict
            failed = [c.name.value for c in first.checks if not c.passed]
            prompt = revision_prompt(base, raw, first.reason, failed, self.revision_context(draft))
            draft, raw, usage, latency = self.structured("revise", prompt)
            revised = True
            self.emit(
                StepKind.REVISE,
                f"Revised the finding as the monitor asked: {LABEL_WORDS[draft.label]}, citing "
                f"{_where(draft.citation_instrument_ref, draft.citation_para)}.",
                latency_ms=latency,
                usage=usage,
            )
            results.append(self.monitor(draft))

        self.stage = StepKind.FINAL
        verdicts = self.read_verdicts + [r.verdict for r in results]
        redirected = revised or any(v.action == MonitorAction.REVISE for v in self.read_verdicts)
        finding = self.finding(draft, results[-1], redirected)
        outcome = {
            MonitorStatus.PASSED: "passed the monitor",
            MonitorStatus.REVISED: (
                "passed the monitor after one revision"
                if revised
                else "passed the monitor after it redirected a read"
            ),
            MonitorStatus.NEEDS_REVIEW: (
                "stopped by the monitor; needs human review"
                if verdicts[-1].action == MonitorAction.STOP
                else "needs human review"
            ),
        }[finding.monitor_status]
        self.emit(
            StepKind.FINAL,
            f"Final finding for clause {self.clause.id}: {LABEL_WORDS[finding.label]}, {outcome}.",
        )
        return finding, verdicts, redirected


def _total(values: list[int | None]) -> int | None:
    return None if any(v is None for v in values) else sum(v for v in values if v is not None)


def review_clause(
    llm: LLM,
    settings: Settings,
    corpus: Corpus,
    clause: Clause,
    emit: Emit,
    *,
    next_seq: Callable[[], int] | None = None,
    today: date | None = None,
    run_id: str | None = None,
) -> ReviewOutcome:
    """Review one clause: research with tools, draft, monitor, at most one revision, final finding.

    `next_seq` numbers the steps (pass one SeqCounter per run); by default the clause numbers its own from 1.
    `today` defaults to the date in India. Raises ClauseReviewError on any failure, after emitting a final
    step that says so.
    """
    started = time.perf_counter()
    review = _Review(
        llm,
        settings,
        corpus,
        clause,
        emit,
        next_seq if next_seq is not None else SeqCounter(),
        today if today is not None else datetime.now(IST).date(),
        run_id,
    )
    try:
        finding, verdicts, revised = review.run()
    except (LLMError, MonitorError, CorpusError, AgentError) as e:
        stage = review.stage
        review.emit(
            StepKind.FINAL,
            f"Review of clause {clause.id} failed at the {stage.value} step: {type(e).__name__}.",
        )
        raise ClauseReviewError(clause.id, stage, f"{type(e).__name__}: {e}") from e
    costs = [u.cost_usd_estimate for u in review.usage]
    return ReviewOutcome(
        finding=finding,
        verdicts=verdicts,
        revised=revised,
        stopped=verdicts[-1].action == MonitorAction.STOP,
        tool_calls=review.tool_calls,
        input_tokens=_total([u.input_tokens for u in review.usage]),
        output_tokens=_total([u.output_tokens for u in review.usage]),
        cost_usd_estimate=None if any(c is None for c in costs) else sum(c for c in costs if c is not None),
        latency_ms=_ms(started),
    )
