// Display formats shared by the shell and the Start, Audit and Evaluation screens.
import { withdrawalOf } from "../data/convert";
import { formatIst } from "../data/selectors";
import type { Citation, RunDoc } from "../data/types";

const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})/;

/**
 * "15 Jul 2026" for an ISO date such as the corpus as-on date. A value that is not an ISO date is shown as
 * stored rather than guessed at.
 */
export function formatDate(iso: string): string {
  const m = ISO_DATE.exec(iso);
  if (!m) return iso;
  const d = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])));
  if (Number.isNaN(d.getTime())) return iso;
  return new Intl.DateTimeFormat("en-GB", { timeZone: "UTC", day: "2-digit", month: "short", year: "numeric" }).format(d);
}

/** When a saved run was recorded: its start time, with the date, in IST. */
export function recordedAt(run: RunDoc): string {
  return formatIst(run.started_at, true);
}

/** "Open saved run (recorded 16 Oct 2026, 12:00:00 IST)": the honest label for every saved run (UI_SPEC 5). */
export function savedRunLabel(run: RunDoc): string {
  return `Open saved run (recorded ${recordedAt(run)})`;
}

/**
 * How current the RBI text behind a run or a citation is. Two different dates, never merged:
 * - `updatedAsOn`: RBI's own "updated as on" date of an instrument in force (null when the data predates the field);
 * - `fetchedOn`: the day Argus fetched the RBI pages (snapshot_date / corpus_as_on).
 * A withdrawn instrument instead carries when and by which circular it was withdrawn.
 */
export type LibraryDates =
  | { kind: "in_force"; updatedAsOn: string | null; fetchedOn: string }
  | { kind: "withdrawn"; withdrawnOn: string; withdrawnBy: string; fetchedOn: string };

/**
 * The one wording for library dates, used by the top bar, the Start footer and the source pane:
 * "RBI text as updated on 15 Jul 2026 · fetched 16 Oct 2026", or for a withdrawn instrument
 * "Withdrawn 28 Nov 2025 by RBI/2025-26/100 · fetched 16 Oct 2026".
 */
export function libraryLabel(d: LibraryDates): string {
  const fetched = `fetched ${formatDate(d.fetchedOn)}`;
  if (d.kind === "withdrawn") return `Withdrawn ${formatDate(d.withdrawnOn)} by ${d.withdrawnBy} · ${fetched}`;
  return d.updatedAsOn ? `RBI text as updated on ${formatDate(d.updatedAsOn)} · ${fetched}` : `RBI text ${fetched}`;
}

/** The library dates of a run: the in-force instrument's update date and the fetch date. Null when none is recorded. */
export function runLibrary(run: RunDoc): LibraryDates | null {
  if (run.corpus_as_on.trim() === "") return null;
  return { kind: "in_force", updatedAsOn: run.rbi_as_on ?? null, fetchedOn: run.corpus_as_on };
}

/** The library dates of one citation: in force with its update date, or withdrawn with when and by what. */
export function citationLibrary(c: Citation): LibraryDates {
  const w = withdrawalOf(c);
  return w
    ? { kind: "withdrawn", withdrawnOn: w.on, withdrawnBy: w.by, fetchedOn: c.snapshot_date }
    : { kind: "in_force", updatedAsOn: c.updated_as_on ?? null, fetchedOn: c.snapshot_date };
}
