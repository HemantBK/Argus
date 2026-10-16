// @vitest-environment jsdom
// Audit log in every state (UI_SPEC 3.3, 6).
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { finding, run, T0 } from "../data/fixtures.test-util";
import { formatIst } from "../data/selectors";
import { auditEvent, live } from "../shell/screens.test-util";
import { actionLabel } from "./auditRows";
import { AuditView } from "./AuditScreen";

afterEach(cleanup);

const runDoc = run({ clauses: [{ clause_id: "c2", title: "FAKE grievance officer", text: "FAKE clause" }] });
const edited = finding({
  clause_id: "c2",
  rationale: "FAKE agent rationale",
  signoff: { decision: "edit", by: "FAKE-uid", at: T0, note: "", edited_rationale: "FAKE officer rationale" },
});
const events = [
  auditEvent(1, { actor: "agent", event: "run_started", detail: "FAKE run started" }),
  auditEvent(2, { actor: "monitor", event: "step_monitor", clause_id: "c2", model: "FAKE-monitor-model", detail: "FAKE monitor verdict" }),
  auditEvent(3, { actor: "officer:FAKE-uid", event: "signoff_edit", clause_id: "c2", detail: "FAKE note" }),
];

describe("AuditView", () => {
  it("lists every event chronologically with time, actor, action, clause, detail and model", () => {
    render(<AuditView source="runs" run={live.ready(runDoc)} audit={live.ready([events[2], events[0], events[1]].flatMap((e) => (e ? [e] : [])))} findings={live.ready([edited])} />);
    const table = screen.getByRole("table");
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["Time (IST)", "Actor", "Action", "Clause", "Detail", "Model"]);
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(events.length);
    const cells = (i: number) => within(rows[i] as HTMLElement).getAllByRole("cell").map((c) => c.textContent);
    expect(cells(0)[0]).toBe(formatIst(events[0]?.at ?? T0));
    expect(cells(0)[2]).toBe(actionLabel("run_started"));
    expect(cells(1)).toEqual([formatIst(events[1]?.at ?? T0), "Monitor", actionLabel("step_monitor"), "c2 · FAKE grievance officer", "FAKE monitor verdict", "FAKE-monitor-model"]);
    expect(cells(2)[1]).toBe("Officer");
  });

  it("shows before and after for an officer edit", () => {
    render(<AuditView source="runs" run={live.ready(runDoc)} audit={live.ready(events)} findings={live.ready([edited])} />);
    expect(screen.getByText("Before (Argus)").nextElementSibling?.textContent).toBe(edited.rationale);
    expect(screen.getByText("After (officer)").nextElementSibling?.textContent).toBe(edited.signoff?.edited_rationale);
  });

  it("shows skeleton rows while loading", () => {
    render(<AuditView source="samples" run={live.loading} audit={live.loading} findings={live.loading} />);
    expect(screen.getByRole("table").getAttribute("aria-busy")).toBe("true");
    expect(document.querySelectorAll(".audit-skeleton").length).toBeGreaterThan(0);
  });

  it("shows a read failure in plain English", () => {
    render(<AuditView source="runs" run={live.ready(runDoc)} audit={live.error("FAKE permission-denied")} findings={live.ready([])} />);
    expect(screen.getByRole("alert").textContent).toMatch(/could not be read.*FAKE permission-denied/s);
  });

  it("says when a run does not exist or has no events yet", () => {
    render(<AuditView source="samples" run={live.ready(null)} audit={live.ready([])} findings={live.ready([])} />);
    expect(screen.getByRole("alert").textContent).toBe("This saved run does not exist.");
    expect(screen.getByText(/No audit events have been recorded/)).toBeTruthy();
  });

  it("labels a saved run as read-only", () => {
    render(<AuditView source="samples" run={live.ready(runDoc)} audit={live.ready(events)} findings={live.ready([edited])} />);
    expect(screen.getByText(/This is a saved run: it is read-only/)).toBeTruthy();
  });
});
