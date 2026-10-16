// Audit log (UI_SPEC 3.3): every AI step and officer decision of a run, chronological, nothing hidden.
// Columns: time (IST) · actor · action · clause · detail · model. Streams live while a run is in progress.
import type { ReactNode } from "react";
import { ErrorBanner } from "../ErrorBoundary";
import { useAudit, useFindings, useRun, type LiveRead } from "../data/hooks";
import { formatIst } from "../data/selectors";
import type { AuditDoc, FindingDoc, RunDoc, RunSource } from "../data/types";
import { auditRows, type AuditRow } from "./auditRows";
import "./audit.css";

const COLUMNS = ["Time (IST)", "Actor", "Action", "Clause", "Detail", "Model"] as const;

export interface AuditViewProps {
  source: RunSource;
  run: LiveRead<RunDoc | null>;
  audit: LiveRead<AuditDoc[]>;
  findings: LiveRead<FindingDoc[]>;
}

function SkeletonRows(): ReactNode {
  return (
    <>
      {[0, 1, 2, 3, 4].map((i) => (
        <tr key={i} className="audit-skeleton" aria-hidden="true">
          {COLUMNS.map((c) => (
            <td key={c}>
              <span className="skeleton-line" />
            </td>
          ))}
        </tr>
      ))}
    </>
  );
}

function Row({ row }: { row: AuditRow }): ReactNode {
  return (
    <tr className={`audit-row audit-actor-${row.actorKind}`}>
      <td className="mono audit-time">{formatIst(row.at)}</td>
      <td>{row.actor}</td>
      <td>{row.action}</td>
      <td>
        {row.clauseId === null ? (
          <span className="text-muted" aria-label="No clause">
            —
          </span>
        ) : (
          <>
            <span className="mono">{row.clauseId}</span>
            {row.clauseTitle && <span className="audit-clause-title"> · {row.clauseTitle}</span>}
          </>
        )}
      </td>
      <td className="audit-detail">
        {row.detail.trim() !== "" ? row.detail : <span className="text-muted">No detail recorded</span>}
        {row.edit && (
          <dl className="audit-edit">
            <dt>Before (Argus)</dt>
            <dd>{row.edit.before}</dd>
            <dt>After (officer)</dt>
            <dd>{row.edit.after}</dd>
          </dl>
        )}
      </td>
      <td className="mono">
        {row.model ?? (
          <span className="text-muted" aria-label="No model">
            —
          </span>
        )}
      </td>
    </tr>
  );
}

export function AuditView({ source, run, audit, findings }: AuditViewProps): ReactNode {
  const errors = [run, audit, findings].flatMap((l) => (l.status === "error" ? [l.error] : []));
  const runDoc = run.status === "ready" ? run.data : null;
  const missing = run.status === "ready" && run.data === null;
  const loading = audit.status === "loading" || audit.status === "idle" || findings.status === "loading" || findings.status === "idle";
  const rows =
    audit.status === "ready" && findings.status === "ready" ? auditRows(audit.data, findings.data, runDoc?.clauses ?? []) : [];
  const live = runDoc?.status === "running";

  return (
    <div className="page">
      <h1>Audit log</h1>
      <p className="page-intro">
        Every step Argus and its monitor recorded, and every decision an officer made, in the order they happened.
        {source === "samples" ? " This is a saved run: it is read-only." : ""}
      </p>
      {errors.map((e) => (
        <ErrorBanner key={e.message} title="Part of the audit log could not be read." error={e} />
      ))}
      {missing && errors.length === 0 && (
        <p className="run-banner" role="alert">
          {source === "runs"
            ? "This live run has not been created yet, or it belongs to another browser. Live runs are visible only to the browser that started them."
            : "This saved run does not exist."}
        </p>
      )}
      <div className="audit-table-wrap">
        <table className="audit-table" aria-busy={loading ? "true" : "false"}>
          <caption className="visually-hidden">
            Audit log, oldest first{live ? ", updating while the run is in progress" : ""}
          </caption>
          <thead>
            <tr>
              {COLUMNS.map((c) => (
                <th key={c} scope="col">
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody aria-live={live ? "polite" : undefined}>
            {loading && errors.length === 0 ? <SkeletonRows /> : rows.map((r) => <Row key={r.key} row={r} />)}
          </tbody>
        </table>
      </div>
      {!loading && errors.length === 0 && rows.length === 0 && <p className="text-muted">No audit events have been recorded for this run yet.</p>}
    </div>
  );
}

export function AuditScreen({ source, runId }: { source: RunSource; runId: string }): ReactNode {
  const run = useRun(runId, source);
  const audit = useAudit(runId, source, run);
  const findings = useFindings(runId, source, run);
  return <AuditView source={source} run={run} audit={audit} findings={findings} />;
}
