// Monitor badge (UI_SPEC 3.2.1, 16.2). The state comes from recorded verdicts (selectors.badgeFor); the detail
// opens on click (and its reason shows on hover) and lists only what was recorded.
import { useId, useState, type ReactNode } from "react";
import { CHECK_TEXT, type Badge, type InjectionCatch } from "../data/selectors";
import type { FindingDoc, StepDoc } from "../data/types";
import { paraLabel } from "./view";

const TONE: Record<Badge["state"], string> = {
  verified: "ok",
  revised: "outdated",
  stopped: "gap",
  needs_review: "review",
  failed: "review",
};

interface Props {
  badge: Badge;
  finding: FindingDoc;
  steps: readonly StepDoc[];
  injection: InjectionCatch | null;
  /** Start with the detail open (tests, and the Stopped state, which a judge must see without hunting). */
  defaultOpen?: boolean;
}

export function MonitorBadge({ badge, finding, steps, injection, defaultOpen }: Props): ReactNode {
  const [open, setOpen] = useState(defaultOpen ?? badge.state === "stopped");
  const detailId = useId();
  return (
    <div className="badge-block">
      <button
        type="button"
        className={`badge badge-${TONE[badge.state]}`}
        aria-expanded={open}
        aria-controls={detailId}
        title={badge.reason}
        onClick={() => {
          setOpen((o) => !o);
        }}
      >
        <span className="badge-icon" aria-hidden="true">
          {badge.icon}
        </span>
        <span>{badge.label}</span>
        <span className="badge-toggle">{open ? "Hide why" : "Why?"}</span>
      </button>
      <div id={detailId} className="badge-detail" hidden={!open}>
        <BadgeDetail badge={badge} finding={finding} steps={steps} injection={injection} />
      </div>
    </div>
  );
}

function BadgeDetail({ badge, finding, steps, injection }: Omit<Props, "defaultOpen">): ReactNode {
  switch (badge.state) {
    case "verified":
      return (
        <>
          <p>{badge.reason}</p>
          {badge.verifiedChecks.length > 0 && (
            <ul className="check-list">
              {badge.verifiedChecks.map((name) => (
                <li key={name}>
                  <span aria-hidden="true">✓ </span>
                  {CHECK_TEXT[name]}
                </li>
              ))}
            </ul>
          )}
        </>
      );
    case "revised": {
      const revise = badge.verdicts.find((v) => v.verdict.action === "revise");
      const reviseStep = revise ? steps.find((s) => s.seq === revise.seq) : undefined;
      return (
        <>
          <dl className="before-after">
            <dt>Before</dt>
            <dd>
              {badge.failedChecks.length > 0
                ? badge.failedChecks.map((c) => (
                    <span key={c.name} className="block">
                      <strong>{CHECK_TEXT[c.name]}: failed.</strong> {c.detail}
                    </span>
                  ))
                : (reviseStep?.summary ?? "The failing check was not recorded.")}
            </dd>
            <dt>After</dt>
            <dd>
              {finding.citation ? (
                <>
                  Cites {finding.citation.instrument}, <span className="mono">{paraLabel(finding.citation)}</span>
                </>
              ) : (
                "No citation after the revision."
              )}
            </dd>
          </dl>
          <p className="text-muted">Monitor's reason: {badge.reason}</p>
        </>
      );
    }
    case "stopped":
      return (
        <>
          <p>
            <strong>Reason:</strong> {badge.reason}
          </p>
          {injection && (
            <figure className="injection">
              <figcaption>Text found in the uploaded policy:</figcaption>
              <blockquote>
                <mark>“{injection.quote}”</mark>
              </blockquote>
            </figure>
          )}
          {badge.failedChecks.length > 0 && (
            <ul className="check-list">
              {badge.failedChecks.map((c) => (
                <li key={c.name}>
                  <span aria-hidden="true">✗ </span>
                  <strong>{CHECK_TEXT[c.name]}: failed.</strong> {c.detail}
                </li>
              ))}
            </ul>
          )}
          <p>
            <strong>What Argus did not do:</strong>{" "}
            {injection
              ? "it did not follow that instruction and did not mark the clause compliant."
              : "it did not conclude anything for this clause."}{" "}
            The decision is yours.
          </p>
        </>
      );
    case "needs_review":
      return (
        <>
          <p>{badge.reason}</p>
          {badge.failedChecks.length > 0 && (
            <ul className="check-list">
              {badge.failedChecks.map((c) => (
                <li key={c.name}>
                  <strong>{CHECK_TEXT[c.name]}: not met.</strong> {c.detail}
                </li>
              ))}
            </ul>
          )}
        </>
      );
    case "failed":
      return (
        <>
          <p>The review of this clause did not complete. It needs a person to review it.</p>
          <p className="text-muted">Recorded error: {badge.reason}</p>
        </>
      );
  }
}
