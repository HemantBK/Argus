// libraryLabel: the one wording for "how current is this RBI text", in both states, from the real library's
// instrument records (backend/tests/fixtures/corpus/instruments.json, the same records the backend tests load).
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { citation, run } from "../data/fixtures.test-util";
import { citationLibrary, formatDate, libraryLabel, runLibrary, type LibraryDates } from "./format";

interface Instrument {
  instrument_ref: string;
  in_force: boolean;
  updated_as_on: string | null;
  snapshot_date: string;
  valid_to: string | null;
  withdrawn_by: string | null;
}

const instruments = JSON.parse(
  readFileSync(new URL("../../../backend/tests/fixtures/corpus/instruments.json", import.meta.url), "utf8"),
) as Instrument[];
const inForce = instruments.find((i) => i.in_force);
const withdrawn = instruments.find((i) => !i.in_force && i.instrument_ref === "RBI/2025-26/36");
if (!inForce?.updated_as_on || !withdrawn?.valid_to || !withdrawn.withdrawn_by) throw new Error("corpus fixture is missing its dates");

/** A citation of an instrument, with the fields the backend copies from its record (agent/loop.py). */
const citeOf = (i: Instrument) =>
  citation({
    instrument_ref: i.instrument_ref,
    in_force: i.in_force,
    snapshot_date: i.snapshot_date,
    updated_as_on: i.in_force ? i.updated_as_on : null,
    withdrawn_by: i.withdrawn_by,
    withdrawn_on: i.valid_to,
  });

describe("libraryLabel", () => {
  it("an instrument in force: RBI's update date, then the fetch date", () => {
    const label = libraryLabel(citationLibrary(citeOf(inForce)));
    expect(label).toBe(`RBI text as updated on ${formatDate(inForce.updated_as_on ?? "")} · fetched ${formatDate(inForce.snapshot_date)}`);
    expect(label).toBe("RBI text as updated on 15 Jul 2026 · fetched 16 Oct 2026");
  });

  it("a withdrawn instrument: when and by which circular, then the fetch date", () => {
    const label = libraryLabel(citationLibrary(citeOf(withdrawn)));
    expect(label).toBe(`Withdrawn ${formatDate(withdrawn.valid_to ?? "")} by ${withdrawn.withdrawn_by ?? ""} · fetched ${formatDate(withdrawn.snapshot_date)}`);
    expect(label).toBe("Withdrawn 28 Nov 2025 by RBI/2025-26/100 · fetched 16 Oct 2026");
  });

  it("a run: its rbi_as_on and corpus_as_on, and only the fetch date when rbi_as_on was not recorded", () => {
    const r = run({ rbi_as_on: inForce.updated_as_on, corpus_as_on: inForce.snapshot_date });
    expect(runLibrary(r)).toEqual<LibraryDates>({ kind: "in_force", updatedAsOn: inForce.updated_as_on, fetchedOn: inForce.snapshot_date });
    expect(libraryLabel(runLibrary({ ...r, rbi_as_on: null }) as LibraryDates)).toBe(`RBI text fetched ${formatDate(inForce.snapshot_date)}`);
    expect(runLibrary({ ...r, corpus_as_on: "" })).toBeNull();
  });

  it("never says snapshot", () => {
    for (const i of instruments) expect(libraryLabel(citationLibrary(citeOf(i)))).not.toMatch(/snapshot/i);
  });
});
