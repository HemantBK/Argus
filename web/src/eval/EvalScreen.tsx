// Evaluation (UI_SPEC 3.4 + 16.1): one table, conditions × metrics, read from eval_results/latest. Every number
// shows its sample size and the run date; a metric a stored run did not measure says "not measured yet". While
// eval_results/latest is absent the page shows no table: it says no evaluation is published here and points to
// the official evaluation in the repository (officialEvaluation.ts), whose numbers stay there so one source states
// them (never "no evaluation run", which was false once the official runs existed). Below: failure cases and the
// corpus box.
import type { ReactNode } from "react";
import { ErrorBanner } from "../ErrorBoundary";
import { useEvalLatest, type LiveRead } from "../data/hooks";
import { formatDate } from "../shell/format";
import { evalTable, formatMetric, parseEvalResults, type EvalCorpus, type EvalResults } from "./evalResults";
import { EVALUATION_DOC_URL, EVALUATION_EVIDENCE_URL, OFFICIAL_RUNS } from "./officialEvaluation";
import "./eval.css";

export const NOT_MEASURED = "not measured yet";
/** A corpus field while no evaluation is published to this page (the official one is in the repository). */
export const NOT_PUBLISHED = "not published here";
export const NONE_PUBLISHED = "No evaluation has been published to this page.";

export interface EvalViewProps {
  latest: LiveRead<Record<string, unknown> | null>;
  /** RBI fetch date from the saved runs, shown in the corpus box when no evaluation has recorded one. */
  fallbackAsOn: string | null;
}

type Parsed = { status: "loading" } | { status: "error"; error: Error } | { status: "ready"; results: EvalResults | null };

function parse(latest: EvalViewProps["latest"]): Parsed {
  if (latest.status === "idle" || latest.status === "loading") return { status: "loading" };
  if (latest.status === "error") return { status: "error", error: latest.error };
  if (latest.data === null) return { status: "ready", results: null };
  try {
    return { status: "ready", results: parseEvalResults(latest.data) };
  } catch (e) {
    return { status: "error", error: e instanceof Error ? e : new Error(String(e)) };
  }
}

function NotMeasured(): ReactNode {
  return <span className="eval-not-measured">{NOT_MEASURED}</span>;
}

function NotPublished(): ReactNode {
  return <span className="eval-not-measured">{NOT_PUBLISHED}</span>;
}

function ExternalLink({ href, children }: { href: string; children: ReactNode }): ReactNode {
  return (
    <a href={href} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  );
}

/** Shown instead of the table while eval_results/latest is absent. Names the runs; states no number. */
function OfficialEvaluationPointer(): ReactNode {
  return (
    <section className="eval-official" aria-labelledby="official-title">
      <h2 id="official-title">Official evaluation</h2>
      <p>
        {NONE_PUBLISHED} The official evaluation (build <span className="mono">7c3072d</span>, 17 Oct 2026, on Vertex AI; the
        review logic it measured is unchanged in the running build) is published in the repository instead: the method, the results and what they do and do not support in{" "}
        <ExternalLink href={EVALUATION_DOC_URL}>docs/evaluation.md</ExternalLink>, and every run&apos;s raw records in{" "}
        <ExternalLink href={EVALUATION_EVIDENCE_URL}>evidence/2026-10-17/eval-official/</ExternalLink>. Its numbers are kept there, so
        that one source states them.
      </p>
      <p>The official runs:</p>
      <ul className="eval-runs">
        {OFFICIAL_RUNS.map((r) => (
          <li key={r.runId}>
            {r.label}: <span className="mono">{r.runId}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function ResultsTable({ results, loading }: { results: EvalResults | null; loading: boolean }): ReactNode {
  const table = evalTable(results);
  return (
    <div className="eval-table-wrap">
      <table className="eval-table" aria-busy={loading ? "true" : "false"}>
        <caption>
          {results ? `Measured on ${formatDate(results.run_date)}` : "Loading the evaluation…"}
        </caption>
        <thead>
          <tr>
            <th scope="col">Metric</th>
            {table.conditions.map((c) => (
              <th key={c.id} scope="col">
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {table.metrics.map((m) => (
            <tr key={m.id}>
              <th scope="row">{m.label}</th>
              {table.conditions.map((c) => {
                const cell = table.cell(m.id, c.id);
                return (
                  <td key={c.id}>
                    {loading ? (
                      <span className="skeleton-line" aria-hidden="true" />
                    ) : cell && cell.value !== null ? (
                      <>
                        <span className="eval-value">{formatMetric(cell.value, m.unit)}</span>
                        <span className="eval-n">on {cell.n_label}</span>
                      </>
                    ) : (
                      <NotMeasured />
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CorpusBox({
  corpus,
  fallbackAsOn,
  published,
}: {
  corpus: EvalCorpus | null;
  fallbackAsOn: string | null;
  /** False while no evaluation is published to this page: missing fields say "not published here". */
  published: boolean;
}): ReactNode {
  const asOn = corpus?.as_on ?? fallbackAsOn;
  const Missing = published ? NotMeasured : NotPublished;
  return (
    <section className="eval-corpus" aria-labelledby="corpus-title">
      <h2 id="corpus-title">RBI library used</h2>
      <dl>
        <dt>As on</dt>
        <dd>{asOn ? formatDate(asOn) : <Missing />}</dd>
        <dt>Documents</dt>
        <dd>
          {corpus ? (
            corpus.documents.length === 0 ? (
              "None recorded"
            ) : (
              <ul>
                {corpus.documents.map((d) => (
                  <li key={d.instrument_ref}>
                    {d.instrument} <span className="mono">({d.instrument_ref})</span>
                  </li>
                ))}
              </ul>
            )
          ) : (
            <Missing />
          )}
        </dd>
        <dt>Paragraphs</dt>
        <dd>{corpus ? corpus.paragraphs : <Missing />}</dd>
        <dt>Supersession rows</dt>
        <dd>{corpus ? corpus.supersession_rows : <Missing />}</dd>
      </dl>
    </section>
  );
}

export function EvalView({ latest, fallbackAsOn }: EvalViewProps): ReactNode {
  const parsed = parse(latest);
  const results = parsed.status === "ready" ? parsed.results : null;
  const nonePublished = parsed.status === "ready" && parsed.results === null;
  return (
    <div className="page">
      <h1>Evaluation</h1>
      <p className="page-intro">
        The same labelled test set reviewed three ways: Gemini alone, the Argus agent without its monitor, and full Argus. This page
        shows numbers only from an evaluation published to it, each with its sample size.
      </p>
      {parsed.status === "error" ? (
        <ErrorBanner title="The evaluation results could not be read, so no numbers are shown." error={parsed.error} />
      ) : nonePublished ? (
        <OfficialEvaluationPointer />
      ) : (
        <ResultsTable results={results} loading={parsed.status === "loading"} />
      )}
      <section className="eval-failures" aria-labelledby="failures-title">
        <h2 id="failures-title">Failure cases</h2>
        {results && results.failure_cases.length > 0 ? (
          <ol>
            {results.failure_cases.map((f) => (
              <li key={f}>{f}</li>
            ))}
          </ol>
        ) : (
          <p className="text-muted">
            {parsed.status === "loading" ? (
              "Loading…"
            ) : nonePublished ? (
              <>
                The official evaluation&apos;s misses are described in{" "}
                <ExternalLink href={EVALUATION_DOC_URL}>docs/evaluation.md</ExternalLink>.
              </>
            ) : parsed.status === "error" ? (
              "Not shown, because the evaluation results could not be read."
            ) : (
              "No failure cases are recorded in this evaluation."
            )}
          </p>
        )}
      </section>
      <CorpusBox corpus={results?.corpus ?? null} fallbackAsOn={fallbackAsOn} published={!nonePublished} />
    </div>
  );
}

export function EvalScreen({ fallbackAsOn }: { fallbackAsOn: string | null }): ReactNode {
  const latest = useEvalLatest();
  return <EvalView latest={latest} fallbackAsOn={fallbackAsOn} />;
}
