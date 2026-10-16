// Bottom drawer (UI_SPEC 3.2): the live trace, one line per recorded step: time, step, tool, result, monitor
// verdict. Monitor REVISE and STOP lines stand out with icon + label + tint. Open while the run is in progress,
// one line once it has finished; the officer's own toggle wins. Clicking a line selects its clause.
import { useEffect, useRef, useState, type ReactNode } from "react";
import { formatIst } from "../data/selectors";
import type { StepDoc } from "../data/types";
import { STEP_KIND_TEXT, plural, traceVerdict } from "./view";

interface Props {
  steps: readonly StepDoc[];
  running: boolean;
  selectedClauseId: string | null;
  onSelectClause: (clauseId: string) => void;
}

export function TraceDrawer({ steps, running, selectedClauseId, onSelectClause }: Props): ReactNode {
  const [userOpen, setUserOpen] = useState<boolean | null>(null);
  const open = userOpen ?? running;
  const listRef = useRef<HTMLOListElement>(null);

  // Keep the newest line in view while the run streams.
  useEffect(() => {
    const el = listRef.current;
    if (open && running && el) el.scrollTop = el.scrollHeight;
  }, [open, running, steps.length]);

  // On a finished run, bring the selected clause's lines into view: its monitor REVISE or STOP line first, so
  // "trace line + badge" are on one screen (UI_SPEC 10, hero moment 3). Lines already in view are not moved.
  useEffect(() => {
    const el = listRef.current;
    if (!open || running || !el || selectedClauseId === null) return;
    const target =
      el.querySelector<HTMLElement>(".trace-line.is-selected.trace-revise, .trace-line.is-selected.trace-stop") ??
      el.querySelector<HTMLElement>(".trace-line.is-selected");
    if (!target) return;
    const top = target.getBoundingClientRect().top - el.getBoundingClientRect().top + el.scrollTop;
    const inView = top >= el.scrollTop && top + target.offsetHeight <= el.scrollTop + el.clientHeight;
    if (!inView) el.scrollTop = Math.max(0, top - target.offsetHeight);
  }, [open, running, selectedClauseId, steps.length]);

  const last = steps.at(-1);
  return (
    <section className={`trace${open ? " is-open" : ""}`} aria-labelledby="trace-heading">
      <div className="trace-bar">
        <h2 id="trace-heading" className="trace-title">
          {running ? "Live trace" : "Trace"}
        </h2>
        <span className="trace-summary text-muted">
          {plural(steps.length, "step")} recorded
          {!open && last && <> · last: {last.summary}</>}
        </span>
        <button
          type="button"
          className="trace-toggle"
          aria-expanded={open}
          aria-controls="trace-lines"
          onClick={() => {
            setUserOpen(!open);
          }}
        >
          {open ? "Collapse trace" : "Show trace"}
        </button>
      </div>
      <ol id="trace-lines" className="trace-lines" ref={listRef} hidden={!open} aria-live="polite" aria-relevant="additions">
        {steps.map((s) => (
          <TraceLine key={s.seq} step={s} selected={s.clause_id !== null && s.clause_id === selectedClauseId} onSelect={onSelectClause} />
        ))}
        {steps.length === 0 && <li className="trace-empty text-muted">No steps recorded yet.</li>}
      </ol>
    </section>
  );
}

function TraceLine({ step, selected, onSelect }: { step: StepDoc; selected: boolean; onSelect: (id: string) => void }): ReactNode {
  const verdict = traceVerdict(step);
  const tone = verdict?.tone ?? "plain";
  const content = (
    <>
      <span className="trace-time mono">{formatIst(step.at)}</span>
      <span className="trace-clause mono">{step.clause_id ?? "run"}</span>
      <span className="trace-kind">{STEP_KIND_TEXT[step.kind]}</span>
      <span className="trace-tool mono">{step.tool ?? ""}</span>
      <span className="trace-text">{step.summary}</span>
      <span className={`trace-verdict trace-verdict-${tone}`}>
        {verdict && (
          <>
            <span aria-hidden="true">{verdict.icon} </span>
            {verdict.label}
          </>
        )}
      </span>
    </>
  );
  const clauseId = step.clause_id;
  return (
    <li className={`trace-line trace-${tone}${selected ? " is-selected" : ""}`}>
      {clauseId !== null ? (
        <button
          type="button"
          className="trace-line-button"
          onClick={() => {
            onSelect(clauseId);
          }}
        >
          {content}
          <span className="visually-hidden"> (select clause {clauseId})</span>
        </button>
      ) : (
        <div className="trace-line-button">{content}</div>
      )}
    </li>
  );
}
