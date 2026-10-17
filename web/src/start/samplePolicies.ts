// The sample policies the API can review, read at build time from the same files the API loads
// (backend/argus_api/samples/policy_*.json, via runs.load_policy). The Start screen shows one card per file, so a
// sample has a card and a Run live button before any saved run of it exists (UI_SPEC 3.1, 16.1). Names, clause
// counts and the Synthetic flag come from the files, never from this source (UI_SPEC 16.6).

export interface SamplePolicy {
  policyId: string;
  title: string;
  clauses: number;
  synthetic: boolean;
  disclaimer: string;
}

export interface SamplePolicies {
  policies: SamplePolicy[];
  /** Files that do not match the shape above: shown as an error on the Start screen, never dropped silently. */
  invalid: Error[];
}

function fail(path: string, what: string): never {
  throw new Error(`${path}: ${what}`);
}

/** Validate one sample policy file. Throws a plain-English error naming the file and the field. */
export function parseSamplePolicy(raw: unknown, path: string): SamplePolicy {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) fail(path, "expected a JSON object");
  const o = raw as Record<string, unknown>;
  const str = (k: string): string => {
    const v = o[k];
    if (typeof v !== "string" || v.trim() === "") fail(path, `${k}: expected a non-empty string`);
    return v;
  };
  if (!Array.isArray(o.clauses) || o.clauses.length === 0) fail(path, "clauses: expected a non-empty list");
  if (typeof o.synthetic !== "boolean") fail(path, "synthetic: expected true or false");
  return { policyId: str("policy_id"), title: str("title"), clauses: o.clauses.length, synthetic: o.synthetic, disclaimer: str("disclaimer") };
}

/** Parse every file, in policy id order; a file that fails is reported in `invalid`. */
export function loadSamplePolicies(files: Readonly<Record<string, unknown>>): SamplePolicies {
  const result: SamplePolicies = { policies: [], invalid: [] };
  for (const [path, raw] of Object.entries(files)) {
    const name = path.slice(path.lastIndexOf("/") + 1);
    try {
      result.policies.push(parseSamplePolicy(raw, `samples/${name}`));
    } catch (e) {
      result.invalid.push(e instanceof Error ? e : new Error(String(e)));
    }
  }
  result.policies.sort((a, b) => a.policyId.localeCompare(b.policyId, "en", { numeric: true }));
  return result;
}

const FILES = import.meta.glob<unknown>("../../../backend/argus_api/samples/policy_*.json", { eager: true, import: "default" });

/** The sample policies bundled into this build. */
export const SAMPLE_POLICIES: SamplePolicies = loadSamplePolicies(FILES);

/**
 * The text of one clause of one bundled sample policy file, or null when the file or clause is missing or
 * malformed. The paste box starts from a real sample clause rather than a second copy of its text.
 */
export function sampleClauseText(files: Readonly<Record<string, unknown>>, policyId: string, clauseId: string): string | null {
  for (const raw of Object.values(files)) {
    if (typeof raw !== "object" || raw === null) continue;
    const o = raw as { policy_id?: unknown; clauses?: unknown };
    if (o.policy_id !== policyId || !Array.isArray(o.clauses)) continue;
    for (const c of o.clauses as unknown[]) {
      if (typeof c !== "object" || c === null) continue;
      const { id, text } = c as { id?: unknown; text?: unknown };
      if (id === clauseId && typeof text === "string" && text.trim() !== "") return text;
    }
  }
  return null;
}

/** Where the "Try your own clause" box starts: sample policy A, clause c2 (the cooling-off clause). */
export const CUSTOM_PREFILL_SOURCE = { policyId: "policy_a", clauseId: "c2" } as const;

/** The text the paste box starts with; empty if this build has no such sample clause. */
export const CUSTOM_PREFILL: string = sampleClauseText(FILES, CUSTOM_PREFILL_SOURCE.policyId, CUSTOM_PREFILL_SOURCE.clauseId) ?? "";
