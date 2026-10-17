// The printed evidence report (report.ts builds it). It exists in the page only while printing: ReviewWorkspace
// renders it on "Print evidence report" or the browser's own print command, and print.css shows it in place of the
// screen. Plain markup, so it prints the same in every browser and reads in order to a screen reader.
import type { ReactNode } from "react";
import type { EvidenceReport as Report, ReportCitation, ReportClause } from "./report";
import { NO_VERIFIED_CITATION } from "./view";
import "./print.css";

function CitationFacts({ c }: { c: ReportCitation }): ReactNode {
  return (
    <dl className="report-facts">
      <dt>Instrument</dt>
      <dd>
        {c.instrument} (<span className="mono">{c.instrumentRef}</span>)
      </dd>
      <dt>Paragraph</dt>
      <dd className="mono">{c.para}</dd>
      <dt>Quote</dt>
      <dd>
        <blockquote className="report-quote">{c.quote}</blockquote>
      </dd>
      <dt>Dates</dt>
      <dd>{c.dates}</dd>
      <dt>Source</dt>
      <dd className="report-url">{c.url}</dd>
    </dl>
  );
}

function Clause({ clause }: { clause: ReportClause }): ReactNode {
  return (
    <section className="report-clause" aria-labelledby={`report-${clause.clauseId}`}>
      <h2 id={`report-${clause.clauseId}`}>
        Clause <span className="mono">{clause.clauseId}</span>
        {clause.title !== clause.clauseId && <> · {clause.title}</>}
      </h2>
      <p className="report-clause-text">{clause.text}</p>

      <dl className="report-facts">
        <dt>Finding</dt>
        <dd>
          <strong>{clause.label}</strong>
        </dd>
        {clause.writtenBy !== null && (
          <>
            <dt>Written by</dt>
            <dd>{clause.backup ? <strong>{clause.writtenBy}</strong> : clause.writtenBy}</dd>
          </>
        )}
        {clause.error !== null && (
          <>
            <dt>Recorded error</dt>
            <dd>
              The review of this clause did not finish, so Argus concluded nothing. A person must review it. {clause.error}
            </dd>
          </>
        )}
        {clause.rationale !== null && (
          <>
            <dt>{clause.officerRationale !== null ? "Argus's original rationale" : "Rationale"}</dt>
            <dd>{clause.rationale}</dd>
          </>
        )}
        {clause.officerRationale !== null && (
          <>
            <dt>Officer's rationale</dt>
            <dd>{clause.officerRationale}</dd>
          </>
        )}
      </dl>

      {clause.citation ? (
        <>
          <h3>Cites</h3>
          <CitationFacts c={clause.citation} />
        </>
      ) : (
        clause.writtenBy !== null &&
        clause.error === null &&
        clause.chain === null && (
          <p className="report-none">{NO_VERIFIED_CITATION}</p>
        )
      )}

      {clause.chain && (
        <>
          <h3>Outdated reference</h3>
          <p>
            Policy cites {clause.chain.cited.instrument}, <span className="mono">{clause.chain.cited.para}</span> →{" "}
            {clause.chain.withdrawal} →{" "}
            {clause.chain.current ? (
              <>
                now in force: {clause.chain.current.instrument}, <span className="mono">{clause.chain.current.para}</span>
              </>
            ) : (
              "no current paragraph found; needs your review"
            )}
          </p>
          <h4>The paragraph the policy cites</h4>
          <CitationFacts c={clause.chain.cited} />
        </>
      )}

      {clause.monitor && (
        <>
          <h3>Monitor: {clause.monitor.badge}</h3>
          {clause.monitor.verdicts.length === 0 ? (
            <p className="report-none">No monitor verdict was recorded for this clause.</p>
          ) : (
            <ol className="report-verdicts">
              {clause.monitor.verdicts.map((v) => (
                <li key={v.seq}>
                  <strong>{v.action}</strong> (step {v.seq}): {v.reason}
                  {v.passed.length > 0 && <span className="report-checks"> Passed: {v.passed.join("; ")}.</span>}
                  {v.failed.length > 0 && <span className="report-checks"> Failed: {v.failed.join("; ")}.</span>}
                </li>
              ))}
            </ol>
          )}
        </>
      )}

      <p className="report-decision">
        <strong>Officer's decision:</strong> {clause.decision}
      </p>
    </section>
  );
}

export function EvidenceReport({ report }: { report: Report }): ReactNode {
  return (
    <article className="evidence-report" aria-labelledby="report-title">
      <header className="report-header">
        <h1 id="report-title">{report.title}</h1>
        <p className="report-policy">{report.policyTitle}</p>
        <ul className="report-meta">
          <li>{report.runKind}</li>
          <li>
            Run <span className="mono">{report.runId}</span>
          </li>
          <li>{report.library}</li>
          <li>{report.models}</li>
          <li>{report.printedAt}</li>
        </ul>
      </header>
      {report.clauses.map((c) => (
        <Clause key={c.clauseId} clause={c} />
      ))}
      <section className="report-summary" aria-labelledby="report-summary-title">
        <h2 id="report-summary-title">Audit summary</h2>
        <ul>
          {report.summary.lines.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      </section>
    </article>
  );
}
