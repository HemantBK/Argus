// Officer sign-off (UI_SPEC 3.2, 16.1, 16.3): Accept · Reject · Edit (Edit last). The decision is posted to the
// API; the updated finding arrives through the findings listener. Saved runs and running runs show the buttons
// disabled with the reason beside them. Failures are shown, never swallowed.
import { useId, useState, type ReactNode } from "react";
import type { SignoffBody } from "../data/api";
import type { FindingDoc, SignoffDecision } from "../data/types";
import { NOTE_MAX_CHARS, RATIONALE_MAX_CHARS } from "../generated/schema";
import { formatIst } from "../data/selectors";
import { DECISION_TEXT, type SignoffAvailability } from "./view";

export type SubmitSignoff = (clauseId: string, body: SignoffBody) => Promise<void>;

type Mode =
  | { kind: "idle" }
  | { kind: "editing"; rationale: string; note: string }
  | { kind: "saving"; decision: SignoffDecision; editing: { rationale: string; note: string } | null }
  | { kind: "error"; message: string; editing: { rationale: string; note: string } | null };

interface Props {
  finding: FindingDoc;
  availability: SignoffAvailability;
  onSubmit: SubmitSignoff;
}

export function SignoffActions({ finding, availability, onSubmit }: Props): ReactNode {
  const [mode, setMode] = useState<Mode>({ kind: "idle" });
  const reasonId = useId();
  const rationaleId = useId();
  const noteId = useId();
  const disabled = !availability.allowed || mode.kind === "saving";
  const editing = mode.kind === "editing" ? mode : mode.kind !== "idle" ? mode.editing : null;

  const submit = async (decision: SignoffDecision, edit: { rationale: string; note: string } | null): Promise<void> => {
    setMode({ kind: "saving", decision, editing: edit });
    const body: SignoffBody =
      decision === "edit" && edit
        ? { decision, note: edit.note, edited_rationale: edit.rationale }
        : { decision, note: "" };
    try {
      await onSubmit(finding.clause_id, body);
      setMode({ kind: "idle" });
    } catch (e) {
      setMode({ kind: "error", message: e instanceof Error ? e.message : String(e), editing: edit });
    }
  };

  const signoff = finding.signoff;

  return (
    <section className="signoff" aria-label="Officer decision">
      {signoff && (
        <p className="signoff-current">
          <strong>{DECISION_TEXT[signoff.decision]}</strong> by the officer at {formatIst(signoff.at, true)}
          {signoff.note && <> · Note: {signoff.note}</>}
        </p>
      )}

      <div className="signoff-buttons">
        <button
          type="button"
          className="primary"
          disabled={disabled}
          aria-describedby={availability.allowed ? undefined : reasonId}
          onClick={() => {
            void submit("accept", null);
          }}
        >
          Accept
        </button>
        <button
          type="button"
          disabled={disabled}
          aria-describedby={availability.allowed ? undefined : reasonId}
          onClick={() => {
            void submit("reject", null);
          }}
        >
          Reject
        </button>
        <button
          type="button"
          disabled={disabled || editing !== null}
          aria-describedby={availability.allowed ? undefined : reasonId}
          onClick={() => {
            setMode({
              kind: "editing",
              rationale: signoff?.edited_rationale ?? finding.rationale,
              note: "",
            });
          }}
        >
          Edit
        </button>
        {mode.kind === "saving" && (
          <span className="text-muted" role="status">
            Saving {DECISION_TEXT[mode.decision].toLowerCase()} decision…
          </span>
        )}
      </div>

      {!availability.allowed && (
        <p id={reasonId} className="signoff-reason">
          {availability.reason}
        </p>
      )}

      {editing && availability.allowed && (
        <form
          className="signoff-edit"
          onSubmit={(e) => {
            e.preventDefault();
            void submit("edit", editing);
          }}
        >
          <p className="text-muted">Argus's rationale stays on record; yours is saved beside it.</p>
          <label htmlFor={rationaleId}>Your rationale</label>
          <textarea
            id={rationaleId}
            rows={4}
            maxLength={RATIONALE_MAX_CHARS}
            required
            value={editing.rationale}
            disabled={mode.kind === "saving"}
            onChange={(e) => {
              setMode({ kind: "editing", rationale: e.target.value, note: editing.note });
            }}
          />
          <label htmlFor={noteId}>Note (optional)</label>
          <input
            id={noteId}
            type="text"
            maxLength={NOTE_MAX_CHARS}
            value={editing.note}
            disabled={mode.kind === "saving"}
            onChange={(e) => {
              setMode({ kind: "editing", rationale: editing.rationale, note: e.target.value });
            }}
          />
          <div className="signoff-buttons">
            <button type="submit" className="primary" disabled={mode.kind === "saving" || !editing.rationale.trim()}>
              Save edit
            </button>
            <button
              type="button"
              disabled={mode.kind === "saving"}
              onClick={() => {
                setMode({ kind: "idle" });
              }}
            >
              Cancel
            </button>
          </div>
        </form>
      )}

      {mode.kind === "error" && (
        <div className="error-banner" role="alert">
          <strong>The decision was not saved.</strong> {mode.message}
        </div>
      )}
    </section>
  );
}
