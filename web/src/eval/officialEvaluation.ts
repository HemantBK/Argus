// Where the official evaluation lives, for the Evaluation page to point at while eval_results/latest is absent.
// The numbers are deliberately not here: docs/evaluation.md states them, so one source does. The run list is the
// "Run | Run id" table of docs/evaluation.md, and officialEvaluation.test.ts fails if the two ever differ.

export const EVALUATION_DOC_URL = "https://github.com/HemantBK/Argus/blob/main/docs/evaluation.md";
export const EVALUATION_EVIDENCE_URL = "https://github.com/HemantBK/Argus/tree/main/evidence/2026-10-17/eval-official";

export interface OfficialRun {
  /** The "Run" column of docs/evaluation.md, word for word. */
  label: string;
  runId: string;
}

export const OFFICIAL_RUNS: readonly OfficialRun[] = [
  { label: "R1 strong, base", runId: "20261017T162059Z-5dc6b4" },
  { label: "R2 strong, hard (headline)", runId: "20261017T154026Z-446cd6" },
  { label: "R2 re-run (reported, not headline)", runId: "20261017T155425Z-cb73b9" },
  { label: "R3 cheaper, base", runId: "20261017T164720Z-f694fd" },
  { label: "R4 cheaper, hard", runId: "20261017T165434Z-a52f4e" },
  { label: "R5 monitor alone", runId: "20261017T170257Z-e32db3" },
];
