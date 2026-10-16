// Display formats shared by the shell and the Start, Audit and Evaluation screens.
import { formatIst } from "../data/selectors";
import type { RunDoc } from "../data/types";

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
