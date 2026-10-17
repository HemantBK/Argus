// Evaluation (UI_SPEC 3.4 + 16.1): one table, conditions × metrics, read from eval_results/latest. Until an
// evaluation is stored every cell says "not measured yet", never a number (PLAN §7). Every number shows its
// sample size and the run date. Below: failure cases and the corpus box.
import type { ReactNode } from "react";
import { ErrorBanner } from "../ErrorBoundary";
import { useEvalLatest, type LiveRead } from "../data/hooks";
import { formatDate } from "../shell/format";
import { evalTable, formatMetric, parseEvalResults, type EvalCorpus, type EvalResults } from "./evalResults";
import "./eval.css";

export const NOT_MEASURED = "not measured yet";

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

function ResultsTable({ results, loading }: { results: EvalResults | null; loading: boolean }): ReactNode {
  const table = evalTable(results);
  return (
    <div className="eval-table-wrap">
      <table className="eval-table" aria-busy={loading ? "true" : "false"}>
        <caption>
          {results ? `Measured on ${formatDate(results.run_date)}` : "No evaluation run has been recorded yet"}
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

function CorpusBox({ corpus, fallbackAsOn }: { corpus: EvalCorpus | null; fallbackAsOn: string | null }): ReactNode {
  const asOn = corpus?.as_on ?? fallbackAsOn;
  return (
    <section className="eval-corpus" aria-labelledby="corpus-title">
      <h2 id="corpus-title">RBI library used</h2>
      <dl>
        <dt>As on</dt>
        <dd>{asOn ? formatDate(asOn) : <NotMeasured />}</dd>
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
            <NotMeasured />
          )}
        </dd>
        <dt>Paragraphs</dt>
        <dd>{corpus ? corpus.paragraphs : <NotMeasured />}</dd>
        <dt>Supersession rows</dt>
        <dd>{corpus ? corpus.supersession_rows : <NotMeasured />}</dd>
      </dl>
    </section>
  );
}

export function EvalView({ latest, fallbackAsOn }: EvalViewProps): ReactNode {
  const parsed = parse(latest);
  const results = parsed.status === "ready" ? parsed.results : null;
  return (
    <div className="page">
      <h1>Evaluation</h1>
      <p className="page-intro">
        The same labelled test set reviewed three ways: Gemini alone, the Argus agent without its monitor, and full Argus. Numbers
        appear only once they are measured, each with its sample size.
      </p>
      {parsed.status === "error" ? (
        <ErrorBanner title="The evaluation results could not be read, so no numbers are shown." error={parsed.error} />
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
            {parsed.status === "loading" ? "Loading…" : "No failure cases recorded yet: they are listed once the evaluation has run."}
          </p>
        )}
      </section>
      <CorpusBox corpus={results?.corpus ?? null} fallbackAsOn={fallbackAsOn} />
    </div>
  );
}

export function EvalScreen({ fallbackAsOn }: { fallbackAsOn: string | null }): ReactNode {
  const latest = useEvalLatest();
  return <EvalView latest={latest} fallbackAsOn={fallbackAsOn} />;
}
