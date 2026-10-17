// The Evaluation page's reading of eval_results/latest. schema.py has no model for it yet (requested in
// BACKEND_REQUESTS.md, item 2), so this module states the shape the page expects and checks every field: a
// document that breaks it is an error on screen, never a half-rendered table. Every number must carry its
// sample size (UI_SPEC 3.4); a metric with no value in a stored run is "not measured yet". Pure; tested in evalResults.test.ts.

export type MetricUnit = "ratio" | "seconds" | "usd" | "count";

export interface EvalCondition {
  id: string;
  label: string;
}

export interface EvalMetric {
  id: string;
  label: string;
  unit: MetricUnit;
}

export interface EvalCell {
  metric: string;
  condition: string;
  /** null = not measured yet. */
  value: number | null;
  /** Sample size in words, e.g. "16 clause cases, 7 attacks". Required whenever value is set. */
  n_label: string | null;
}

export interface EvalCorpus {
  documents: { instrument: string; instrument_ref: string }[];
  as_on: string;
  paragraphs: number;
  supersession_rows: number;
}

export interface EvalResults {
  /** ISO date the evaluation was run. */
  run_date: string;
  conditions: EvalCondition[];
  metrics: EvalMetric[];
  results: EvalCell[];
  corpus: EvalCorpus | null;
  /** Failure cases in plain English. */
  failure_cases: string[];
}

/** UI_SPEC 3.4: the conditions and metrics the table always shows, measured or not. */
export const DEFAULT_CONDITIONS: EvalCondition[] = [
  { id: "plain_gemini", label: "Plain Gemini" },
  { id: "agent_no_monitor", label: "Agent without monitor" },
  { id: "full_argus", label: "Full Argus" },
];

export const DEFAULT_METRICS: EvalMetric[] = [
  { id: "finding_precision", label: "Finding precision", unit: "ratio" },
  { id: "finding_recall", label: "Finding recall", unit: "ratio" },
  { id: "citation_accuracy", label: "Citation accuracy", unit: "ratio" },
  { id: "stale_citation_detection", label: "Stale-citation detection", unit: "ratio" },
  { id: "attack_success_rate", label: "Attack success rate", unit: "ratio" },
  { id: "false_stops", label: "False stops", unit: "count" },
  { id: "latency", label: "Latency per review", unit: "seconds" },
  { id: "cost_per_review", label: "Cost per review", unit: "usd" },
];

export class EvalContractError extends Error {
  override name = "EvalContractError";
  constructor(field: string, problem: string) {
    super(`eval_results/latest.${field}: ${problem}`);
  }
}

type Obj = Record<string, unknown>;

function obj(v: unknown, field: string): Obj {
  if (typeof v !== "object" || v === null || Array.isArray(v)) throw new EvalContractError(field, "expected an object");
  return v as Obj;
}
function arr(v: unknown, field: string): unknown[] {
  if (!Array.isArray(v)) throw new EvalContractError(field, "expected a list");
  return v;
}
function str(o: Obj, key: string, field: string): string {
  const v = o[key];
  if (typeof v !== "string" || v.trim() === "") throw new EvalContractError(`${field}.${key}`, "expected a non-empty string");
  return v;
}
function nonNegative(o: Obj, key: string, field: string): number {
  const v = o[key];
  if (typeof v !== "number" || !Number.isFinite(v) || v < 0) throw new EvalContractError(`${field}.${key}`, "expected a number ≥ 0");
  return v;
}

const UNITS: readonly MetricUnit[] = ["ratio", "seconds", "usd", "count"];

export function parseEvalResults(data: unknown): EvalResults {
  const o = obj(data, "(document)");
  const run_date = str(o, "run_date", "");
  const conditions = arr(o.conditions, "conditions").map((c, i) => {
    const co = obj(c, `conditions[${i}]`);
    return { id: str(co, "id", `conditions[${i}]`), label: str(co, "label", `conditions[${i}]`) };
  });
  const metrics = arr(o.metrics, "metrics").map((m, i) => {
    const mo = obj(m, `metrics[${i}]`);
    const unit = str(mo, "unit", `metrics[${i}]`);
    if (!UNITS.includes(unit as MetricUnit)) throw new EvalContractError(`metrics[${i}].unit`, `unknown unit "${unit}"`);
    return { id: str(mo, "id", `metrics[${i}]`), label: str(mo, "label", `metrics[${i}]`), unit: unit as MetricUnit };
  });
  const conditionIds = new Set(conditions.map((c) => c.id));
  const metricIds = new Set(metrics.map((m) => m.id));
  const results = arr(o.results, "results").map((r, i) => {
    const f = `results[${i}]`;
    const ro = obj(r, f);
    const metric = str(ro, "metric", f);
    const condition = str(ro, "condition", f);
    if (!metricIds.has(metric)) throw new EvalContractError(`${f}.metric`, `"${metric}" is not in metrics`);
    if (!conditionIds.has(condition)) throw new EvalContractError(`${f}.condition`, `"${condition}" is not in conditions`);
    const value = ro.value === null || ro.value === undefined ? null : nonNegative(ro, "value", f);
    const n_label = ro.n_label === null || ro.n_label === undefined ? null : str(ro, "n_label", f);
    if (value !== null && n_label === null) throw new EvalContractError(`${f}.n_label`, "a measured value must say its sample size");
    return { metric, condition, value, n_label };
  });
  let corpus: EvalCorpus | null = null;
  if (o.corpus !== null && o.corpus !== undefined) {
    const c = obj(o.corpus, "corpus");
    corpus = {
      documents: arr(c.documents, "corpus.documents").map((d, i) => {
        const dobj = obj(d, `corpus.documents[${i}]`);
        return {
          instrument: str(dobj, "instrument", `corpus.documents[${i}]`),
          instrument_ref: str(dobj, "instrument_ref", `corpus.documents[${i}]`),
        };
      }),
      as_on: str(c, "as_on", "corpus"),
      paragraphs: nonNegative(c, "paragraphs", "corpus"),
      supersession_rows: nonNegative(c, "supersession_rows", "corpus"),
    };
  }
  const failure_cases = (o.failure_cases === undefined ? [] : arr(o.failure_cases, "failure_cases")).map((x, i) => {
    if (typeof x !== "string" || x.trim() === "") throw new EvalContractError(`failure_cases[${i}]`, "expected a non-empty string");
    return x;
  });
  return { run_date, conditions, metrics, results, corpus, failure_cases };
}

export function formatMetric(value: number, unit: MetricUnit): string {
  switch (unit) {
    case "ratio":
      return new Intl.NumberFormat("en-IN", { style: "percent", maximumFractionDigits: 1 }).format(value);
    case "seconds":
      return `${new Intl.NumberFormat("en-IN", { maximumFractionDigits: 1 }).format(value)} s`;
    case "usd":
      return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumSignificantDigits: 3 }).format(value);
    case "count":
      return new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 }).format(value);
  }
}

export interface EvalTable {
  conditions: EvalCondition[];
  metrics: EvalMetric[];
  /** cell(metricId, conditionId): the recorded cell, or null when nothing was recorded (not measured yet). */
  cell: (metric: string, condition: string) => EvalCell | null;
}

/** The table to draw: the stored conditions and metrics, or the spec's when nothing is stored. */
export function evalTable(results: EvalResults | null): EvalTable {
  if (!results) return { conditions: DEFAULT_CONDITIONS, metrics: DEFAULT_METRICS, cell: () => null };
  const map = new Map(results.results.map((r) => [`${r.metric}\u0000${r.condition}`, r]));
  return {
    conditions: results.conditions,
    metrics: results.metrics,
    cell: (m, c) => map.get(`${m}\u0000${c}`) ?? null,
  };
}
