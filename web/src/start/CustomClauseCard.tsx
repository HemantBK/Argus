// "Try your own clause" (D-60 track C): one pasted clause, reviewed live exactly like a sample run. The box
// starts from sample policy A clause c2 (read from the bundled sample file, never a second copy here). The
// officer must tick that the text is synthetic or anonymised; the API rejects obvious identifiers, keeps the run
// private to its owner and deletes it within 48 hours. Length is measured as the API measures it (clauseLength).
import { useId, useState, type ReactNode } from "react";
import { CUSTOM_CLAUSE_MAX_CHARS, CUSTOM_CLAUSE_MIN_CHARS, CUSTOM_POLICY_ID, clauseLength, type CustomClause } from "../data/api";
import { rememberSentDraft, sentDraft } from "./customDraft";

export const CUSTOM_NOTICE =
  "Synthetic or anonymised text only. Obvious identifiers are rejected. Kept in your private run and deleted within 48 hours. Not legal advice.";
export const ATTEST_LABEL = "This text is synthetic or anonymised and contains no personal data";

const fmt = (n: number): string => n.toLocaleString("en-IN");

/** Why the clause cannot be sent yet, in plain English, or null when it can. */
function blocker(length: number, attested: boolean): string | null {
  if (length < CUSTOM_CLAUSE_MIN_CHARS) return `Too short: a clause needs at least ${fmt(CUSTOM_CLAUSE_MIN_CHARS)} characters.`;
  if (length > CUSTOM_CLAUSE_MAX_CHARS) {
    return `Too long: one clause of at most ${fmt(CUSTOM_CLAUSE_MAX_CHARS)} characters. Remove ${fmt(length - CUSTOM_CLAUSE_MAX_CHARS)}.`;
  }
  if (!attested) return "Tick the box to confirm the text contains no personal data.";
  return null;
}

export interface CustomClauseCardProps {
  /** The text the box starts with (sample policy A clause c2 in the app). */
  prefill: string;
  onRun: (policyId: string, custom: CustomClause) => void;
}

export function CustomClauseCard({ prefill, onRun }: CustomClauseCardProps): ReactNode {
  const [text, setText] = useState<string>(() => sentDraft() ?? prefill);
  const [attested, setAttested] = useState(false);
  const id = useId();
  const length = clauseLength(text);
  const problem = blocker(length, attested);
  const lengthOk = length >= CUSTOM_CLAUSE_MIN_CHARS && length <= CUSTOM_CLAUSE_MAX_CHARS;

  return (
    <section className="custom-clause" aria-labelledby={`${id}-title`}>
      <h2 id={`${id}-title`}>Try your own clause</h2>
      <p className="custom-clause-notice">{CUSTOM_NOTICE}</p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (problem !== null) return;
          rememberSentDraft(text);
          onRun(CUSTOM_POLICY_ID, { clauseText: text, attest: true });
        }}
      >
        <label htmlFor={`${id}-text`} className="custom-clause-label">
          One clause of a digital-lending policy
        </label>
        <textarea
          id={`${id}-text`}
          className="custom-clause-text"
          rows={6}
          value={text}
          onChange={(e) => {
            setText(e.target.value);
          }}
          aria-describedby={`${id}-count`}
          aria-invalid={!lengthOk}
          spellCheck={false}
        />
        <p id={`${id}-count`} className={`custom-clause-count${lengthOk ? "" : " custom-clause-count-bad"}`} aria-live="polite">
          {fmt(length)} of {fmt(CUSTOM_CLAUSE_MIN_CHARS)}–{fmt(CUSTOM_CLAUSE_MAX_CHARS)} characters
        </p>
        <label className="custom-clause-attest">
          <input
            type="checkbox"
            required
            checked={attested}
            onChange={(e) => {
              setAttested(e.target.checked);
            }}
          />{" "}
          {ATTEST_LABEL}
        </label>
        {problem !== null && (
          <p id={`${id}-problem`} className="custom-clause-problem text-muted">
            {problem}
          </p>
        )}
        <button type="submit" className="primary custom-clause-run" disabled={problem !== null} aria-describedby={problem !== null ? `${id}-problem` : undefined}>
          Review
        </button>
      </form>
    </section>
  );
}
