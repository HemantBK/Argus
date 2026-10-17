// The Evaluation page's run list (officialEvaluation.ts) must be the "Run | Run id" table of docs/evaluation.md,
// and every run must have its records in the evidence folder the page links to. If the docs change, this fails
// until the page says the same.
import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { EVALUATION_DOC_URL, EVALUATION_EVIDENCE_URL, OFFICIAL_RUNS } from "./officialEvaluation";

const repo = fileURLToPath(new URL("../../../", import.meta.url));
const read = (path: string): string => readFileSync(`${repo}${path}`, "utf8");
const GITHUB = "https://github.com/HemantBK/Argus/";

/** The rows of the first markdown table whose header starts "| Run | Run id |": [Run, Run id] per row. */
function docRunTable(markdown: string): { label: string; runId: string }[] {
  const lines = markdown.split("\n");
  const start = lines.findIndex((l) => l.startsWith("| Run | Run id |"));
  if (start < 0) throw new Error('docs/evaluation.md has no "| Run | Run id |" table');
  const rows: { label: string; runId: string }[] = [];
  for (const line of lines.slice(start + 2)) {
    if (!line.startsWith("|")) break;
    const cells = line.split("|").map((c) => c.trim());
    rows.push({ label: cells[1] ?? "", runId: (cells[2] ?? "").replace(/`/g, "") });
  }
  return rows;
}

describe("official evaluation pointer", () => {
  it("lists exactly the runs of docs/evaluation.md, in its order and with its labels", () => {
    expect(OFFICIAL_RUNS).toEqual(docRunTable(read("docs/evaluation.md")));
  });

  it("names runs whose records are in the linked evidence folder and in OFFICIAL_RESULTS.md", () => {
    const official = read("evidence/2026-10-17/eval-official/OFFICIAL_RESULTS.md");
    for (const r of OFFICIAL_RUNS) {
      expect(existsSync(`${repo}evidence/2026-10-17/eval-official/results/${r.runId}/results.json`), r.runId).toBe(true);
      expect(official).toContain(r.runId);
    }
  });

  it("links to files that exist in this repository", () => {
    expect(EVALUATION_DOC_URL.startsWith(`${GITHUB}blob/main/`)).toBe(true);
    expect(existsSync(`${repo}${EVALUATION_DOC_URL.slice(`${GITHUB}blob/main/`.length)}`)).toBe(true);
    expect(EVALUATION_EVIDENCE_URL.startsWith(`${GITHUB}tree/main/`)).toBe(true);
    expect(existsSync(`${repo}${EVALUATION_EVIDENCE_URL.slice(`${GITHUB}tree/main/`.length)}/README.md`)).toBe(true);
  });

  it("names the build the official evaluation ran on, as docs/evaluation.md does", () => {
    expect(read("docs/evaluation.md")).toContain("frozen build `7c3072d`");
  });
});
